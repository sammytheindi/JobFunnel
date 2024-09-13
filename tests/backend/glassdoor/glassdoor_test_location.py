import cloudscraper
from requests import Session
from urllib.parse import quote

scraper = cloudscraper.create_scraper()

session = Session()

province_or_state = "CA"
city = "San%20Fransisco"

enc_location = quote(", ".join([city, province_or_state]))
url = "https://www.glassdoor.ca/autocomplete/location?locationTypeFilters=CITY,STATE,COUNTRY&caller=jobs&term={0},%20{1}".format(
    city, province_or_state
)

print(url)

# for _ in range(20):
result = scraper.get(
    # f"https://www.glassdoor.ca/autocomplete/location?locationTypeFilters=CITY,STATE,COUNTRY&caller=jobs&term={quote(city)},%20{quote(province_or_state)}"
    "https://www.glassdoor.ca/autocomplete/location?locationTypeFilters=CITY,STATE,COUNTRY&caller=jobs&term=San%20Francisco,%20CA"
    # url
)

print(result.text)

# "https://www.glassdoor.ca/Job/california-us-python-django-jobs-SRCH_IL.0,13_IS2280_KO14,27.htm"
# "https://www.glassdoor.ca/Job/new-york-ny-software-jobs-SRCH_IL.0,11_IC1132348_KO12,20.htm" $

# "https://www.glassdoor.ca/Job/london-england-uk-software-engineer-jobs-SRCH_IL.0,17_IC2671300_KO18,35.htm"
# C in IC is location type
# The final number is the length of the input. So for example, 35 is london-england-uk-software-engineer
# The penultimate number 18 is the length until the end of the location input. So for example, 18 is london-england-uk-
# The IC/IS is the location code. We can get this from the location search
# The after SRCH_IL.0 does not seem to matter. I am going to stick with 17 for now


"https://www.glassdoor.ca/Job/london-england-uk-software-engineer-jobs-SRCH_IL.0,13_IC2671300_KO18,35.htm"
