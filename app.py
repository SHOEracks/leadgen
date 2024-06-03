import streamlit as st
import streamlit_authenticator as stauth
import csv
import re
import time
import pandas as pd
import requests
import logging
import yaml
import base64
import io

from bs4 import BeautifulSoup
from serpapi import GoogleSearch
from urllib.parse import urlparse, quote_plus, urljoin
from yaml.loader import SafeLoader



# page conf (has to be called as the first st. command in the script)
st.set_page_config(layout="wide", page_title="Lead Generator", page_icon=":call_me_hand:")

# load conifg.yaml from secrets
config = st.secrets["config"]
config = yaml.load(config, Loader=SafeLoader)

# auth widget
authenticator = stauth.Authenticate(
    config['credentials'],
    config['cookie']['name'],
    config['cookie']['key'],
    config['cookie']['expiry_days'],
    config['pre-authorized']
)

# force login
authenticator.login()

# set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# header for requests
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# extract domain from URL or email
def get_domain(address):
    parsed_url = urlparse(address)
    if parsed_url.scheme:  # if it's a URL
        domain = parsed_url.netloc
    else:  # if it's an email
        domain = address.split('@')[-1]

    # remove 'www.' prefix if present
    if domain.startswith('www.'):
        domain = domain[4:]
    return domain


# filter emails based on domain
def filter_emails(emails, url):
    filtered_emails = set()
    for email in emails:
        domain = get_domain(email)
        if domain.endswith(('gmail.com', 'live.com')):
            filtered_emails.add(email)
        elif domain in url:
            filtered_emails.add(email)
    return filtered_emails


# get coordinates from city name using Google Geocoding API
def get_coordinates(city_name, api_key):
    geocode_url = "https://maps.googleapis.com/maps/api/geocode/json?address={}&key={}".format(
        quote_plus(city_name), api_key)
    response = requests.get(geocode_url)
    geocode_data = response.json()

    if geocode_data['status'] == 'OK':
        location = geocode_data['results'][0]['geometry']['location']
        return "@{},{},14z".format(location['lat'], location['lng'])
    else:
        raise ValueError("Could not geocode the city: {}".format(city_name))


# perform Google Maps search
def search_google_maps(api_key, query, location, language, region, start):    
    params = {
        "api_key": api_key,
        "engine": "google_maps",
        "type": "search",
        "google_domain": "google.com",
        "q": query,
        "ll": location,
        "hl": language,
        "gl": region,
        "start": start
    }

    search = GoogleSearch(params)
    results = search.get_dict()
    return results


# scrape emails from a given URL
def scrape_emails_from_url(url):
    emails = set()
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        # extract emails from the current URL
        homepage_emails = set(re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', response.text))
        emails.update(homepage_emails)

        # if emails are found on the homepage, skip scraping contact links
        emails = filter_emails(homepage_emails, url)
        if emails:
            return emails

        # extract links from the page
        links = soup.find_all('a', href=True)

        # filter links to include only contact pages
        contact_links = [link['href'] for link in links if 'contact' in link['href'].lower()]

        # visit contact pages and extract emails
        for contact_link in contact_links:
            if not contact_link.startswith('http'):
                contact_link = urljoin(url, contact_link)
            contact_response = requests.get(contact_link, headers=headers, timeout=10)
            contact_response.raise_for_status()
            contact_soup = BeautifulSoup(contact_response.content, 'html.parser')
            emails.update(set(re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', contact_soup.text)))
            emails = filter_emails(emails, url)
            if emails:
                return emails

    except requests.exceptions.RequestException as e:
        logging.error("Error accessing {}: {}".format(url, e))
    except Exception as e:
        logging.error("An error occurred: {}".format(e))

    return emails

@st.cache_data
def get_csv_header():
    return ['Name', 'Email', 'URL', 'Phone', 'Address']


@st.cache_data
def get_csv_writer():
    output = io.StringIO()
    fieldnames = ['Name', 'Email', 'URL', 'Phone', 'Address']
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    return output, writer


def create_download_link(buffer, filename):
    val = buffer.getvalue().encode()
    b64 = base64.b64encode(val).decode()
    return f'<a href="data:application/octet-stream;base64,{b64}" download="{filename}.csv">Download CSV</a>'


def main():
    if st.session_state["authentication_status"]: # logged in
        authenticator.logout()
        

        serpapi_api_key = st.secrets["SERPAPI_API_KEY"]
        geocoding_api_key = st.secrets["GEOCODING_API_KEY"]
        
        # README 
        st.markdown(f"""
        # Lead Generator

        Hey *{st.session_state["name"]}*, this will allow you to search for business types in a specific city and scrape their contact information, including emails, from their websites. This app uses the Google Maps API to search for the selected business type and the BeautifulSoup library to scrape emails from the websites.
        """)

        # changelog
        with st.expander("Changelog"):
            st.markdown("""
            ### 0.1.1
            - implemented workaround for open issue 8432 (download button reloads params)
            - Removed 'view pins' for simplicity
            - Increased user feedback messages and graceful error handling
            """)

        # user input
        business_type = st.selectbox(
            "Select the type of business",
            ("Funeral home", "Graveyard", "Hospice", "Crematorium"))

        city_name = st.text_input("Enter the city name:", placeholder="Amsterdam")

        log_notice = st.empty() # place holder for notice box

        # formatting for serpapi
        if city_name:
            start = "0"
            query = f"{city_name} {business_type}"
            language = "en"
            region = city_name

            try: # try to get coordinates for user-supplied {city_name}
                location = get_coordinates(city_name, geocoding_api_key)
                log_notice.info(f"Found coordinates for {city_name}, {location}", icon="🍳")
                time.sleep(2) # added for readability
            except ValueError as e:
                log_notice.error(f"Could not get coordinates for {city_name}")
                return

            # Get CSV header
            fieldnames = get_csv_header()

            # Create in-memory CSV writer
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=fieldnames)
            writer.writeheader()

            idx = 0  # Initialize index

            while True:
                # Get Google Maps results
                results = search_google_maps(serpapi_api_key, query, location, language, region, start)

                if results is None:
                    break

                # Parse results
                if 'local_results' in results:
                    for result in results['local_results']:
                        name = result.get('title')
                        url = result.get('website')
                        phone = result.get('phone')
                        address = result.get('address')

                        emails = []
                        if url: # If url, try to scrape the emails
                            emails = scrape_emails_from_url(url)
                            time.sleep(0) # Increase when needed

                        writer.writerow({
                            'Name': name,
                            'Email': ', '.join(emails),
                            'URL': url,
                            'Phone': phone,
                            'Address': address,
                        })
                        idx += 1    # Increment index
                        log_notice.info(f"Processed {idx} {business_type}s...", icon="🍳")

                    if 'serpapi_pagination' in results and 'next' in results['serpapi_pagination']:
                        start = str(int(start) + 20)  # Increment start parameter by 20 for next page
                    else:
                        break   # No more pages
                else:
                    break   # No more results

            if idx == 0:
                log_notice.error("Exhausted all keys in pool, quitting the app", icon="😢")
                #st.stop()
            else:
                log_notice.success(f"Added {idx} {business_type}s in {city_name} to the CSV file", icon="✅")

                # Display the CSV file
                buffer.seek(0)  # Reset buffer position to the start
                df = pd.read_csv(buffer)
                st.dataframe(df)

                # Create download link, we inject because st.download_button is broken atm
                download_url = create_download_link(buffer, f"{city_name}_{business_type}")
                st.markdown(download_url, unsafe_allow_html=True)
    
                # link to google maps with same query (it automagically corrects the coordinates and zoom level based on city name)
                #st.link_button("View pins Google Maps", f"https://www.google.nl/maps/search/{city_name}+{business_type}/{location}/data=!3m1!4b1?entry=ttu") # cannot Iframe this because of google's security policy
    
    # false if incorrect creds
    elif st.session_state["authentication_status"] is False:
        st.error('Username/password is incorrect')
    
    # first time visit and no cookie (30 days --change in config.yaml--)
    elif st.session_state["authentication_status"] is None:
        st.warning('Please enter your username and password')



if __name__ == "__main__":
    main()
