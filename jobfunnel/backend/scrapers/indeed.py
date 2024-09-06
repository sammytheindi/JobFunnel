"""Scraper designed to get jobs from www.indeed.X
"""

import re
from concurrent.futures import ThreadPoolExecutor, wait
from math import ceil
from typing import Any, Dict, List, Optional
from unicodedata import normalize

from bs4 import BeautifulSoup
from requests import Session

from jobfunnel.backend import Job
from jobfunnel.backend.scrapers.base import (
    BaseCANEngScraper,
    BaseScraper,
    BaseUSAEngScraper,
    BaseUKEngScraper,
    BaseFRFreScraper,
    BaseDEGerScraper,
)
from jobfunnel.backend.tools.filters import JobFilter
from jobfunnel.backend.tools.tools import calc_post_date_from_relative_str
from jobfunnel.resources import MAX_CPU_WORKERS, JobField, Remoteness

from jobfunnel.backend.tools.tools import get_webdriver


# pylint: disable=using-constant-test,unused-import
if False:  # or typing.TYPE_CHECKING  if python3.5.3+
    from jobfunnel.config import JobFunnelConfigManager
# pylint: enable=using-constant-test,unused-import

ID_REGEX = re.compile(r"id=\"sj_([a-zA-Z0-9]*)\"")
MAX_RESULTS_PER_INDEED_PAGE = 50
# NOTE: these magic strings stick for both the US and CAN indeed websites...
FULLY_REMOTE_MAGIC_STRING = "&remotejob=032b3046-06a3-4876-8dfd-474eb5e7ed11"
COVID_REMOTE_MAGIC_STRING = "&remotejob=7e3167e4-ccb4-49cb-b761-9bae564a0a63"
REMOTENESS_TO_QUERY = {
    Remoteness.IN_PERSON: "",
    Remoteness.TEMPORARILY_REMOTE: COVID_REMOTE_MAGIC_STRING,
    Remoteness.PARTIALLY_REMOTE: "",
    Remoteness.FULLY_REMOTE: FULLY_REMOTE_MAGIC_STRING,
    Remoteness.ANY: "",
}
REMOTENESS_STR_MAP = {
    "remote": Remoteness.FULLY_REMOTE,
    "temporarily remote": Remoteness.TEMPORARILY_REMOTE,
}


class BaseIndeedScraper(BaseScraper):
    """Scrapes jobs from www.indeed.X"""

    def __init__(
        self, session: Session, config: "JobFunnelConfigManager", job_filter: JobFilter
    ) -> None:
        """Init that contains indeed specific stuff"""
        super().__init__(session, config, job_filter)
        self.max_results_per_page = MAX_RESULTS_PER_INDEED_PAGE
        self.query = "+".join(self.config.search_config.keywords)

        # Log if we can't do their remoteness query (Indeed only has 2 lvls.)
        if self.config.search_config.remoteness == Remoteness.PARTIALLY_REMOTE:
            self.logger.warning("Indeed does not support PARTIALLY_REMOTE jobs")

    @staticmethod
    def checkForRemoteness(text: str) -> Remoteness:
        """Check if a job description contains a remote keyword"""
        REMOTE_KEYWORDS = ["remote", "telecommute", "telework", "work from home", "wfh"]
        IN_PERSON_KEYWORDS = ["in person", "on site", "on-site", "onsite"]

        text = text.lower()

        if any([word in text for word in REMOTE_KEYWORDS]):
            return Remoteness.FULLY_REMOTE
        elif any([word in text for word in IN_PERSON_KEYWORDS]):
            return Remoteness.IN_PERSON
        return Remoteness.UNKNOWN

    @property
    def job_get_fields(self) -> str:
        """Call self.get(...) for the JobFields in this list when scraping a Job

        Override this as needed.
        """
        return [
            JobField.TITLE,
            JobField.COMPANY,
            JobField.LOCATION,
            JobField.KEY_ID,
            JobField.TAGS,
            JobField.POST_DATE,
            JobField.WAGE,
        ]

    @property
    def job_set_fields(self) -> str:
        """Call self.set(...) for the JobFields in this list when scraping a Job

        NOTE: Since this passes the Job we are updating, the order of this list
        matters if set fields rely on each-other.

        Override this as needed.
        """
        return [JobField.RAW, JobField.URL, JobField.DESCRIPTION, JobField.REMOTENESS]

    @property
    def delayed_get_set_fields(self) -> str:
        """Delay execution when getting /setting any of these attributes of a
        job.

        Override this as needed.
        """
        return [JobField.RAW]

    @property
    def high_priority_get_set_fields(self) -> List[JobField]:
        """These get() and/or set() fields will be populated first."""
        return [JobField.URL]

    @property
    def headers(self) -> Dict[str, str]:
        """Session header for indeed.X"""
        return {
            "accept": "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/webp,*/*;q=0.8",
            "accept-encoding": "gzip, deflate, sdch",
            "accept-language": "en-GB,en-US;q=0.8,en;q=0.6",
            "referer": f"https://www.indeed.{self.config.search_config.domain}/",
            "upgrade-insecure-requests": "1",
            "user-agent": self.user_agent,
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }

    def get_job_soups_from_search_result_listings(self) -> List[BeautifulSoup]:
        """Scrapes raw data from a job source into a list of job-soups

        Returns:
            List[BeautifulSoup]: list of jobs soups we can use to make Job init
        """
        # Get the search url
        search_url = self._get_search_url()

        # Parse total results, and calculate the # of pages needed
        pages = self._get_num_search_result_pages(search_url)
        self.logger.info(
            "Found %d pages of search results for query=%s", pages, self.query
        )

        # Init list of job soups
        job_soup_list = []  # type: List[Any]

        # Init threads & futures list FIXME: we should probably delay here too
        threads = ThreadPoolExecutor(max_workers=MAX_CPU_WORKERS)
        try:
            # Scrape soups for all the result pages containing many job listings
            futures = []
            for page in range(0, pages):
                futures.append(
                    threads.submit(
                        self._get_job_soups_from_search_page,
                        search_url,
                        page,
                        job_soup_list,
                    )
                )

            # Wait for all scrape jobs to finish
            wait(futures)

        finally:
            threads.shutdown()

        return job_soup_list

    def get(self, parameter: JobField, soup: BeautifulSoup) -> Any:
        """Get a single job attribute from a soup object by JobField"""

        if parameter == JobField.TITLE:
            # Extract job title from the anchor tag under h2
            title_element = soup.find("h2", class_="jobTitle")
            if title_element:
                return title_element.get_text(strip=True)
            return None

        elif parameter == JobField.COMPANY:
            # Extract company name from the span with data-testid="company-name"
            company_element = soup.find("span", attrs={"data-testid": "company-name"})
            if company_element:
                return company_element.get_text(strip=True)
            return None

        elif parameter == JobField.LOCATION:
            # Extract location from the span with data-testid="text-location"
            location_element = soup.find("div", attrs={"data-testid": "text-location"})
            if location_element:
                return location_element.get_text(strip=True)
            return None

        elif parameter == JobField.TAGS:
            # In the current HTML structure, there might not be explicit tags,
            # so this section can return empty for now unless tags are found in another part
            return []

        elif parameter == JobField.REMOTENESS:
            # Extract remoteness info (if present) based on your previous logic
            # If you spot a specific tag, update this part.
            remoteness_element = soup.find("span", class_="remote")
            if remoteness_element:
                remoteness_str = remoteness_element.get_text(strip=True).lower()
                if remoteness_str in REMOTENESS_STR_MAP:
                    return REMOTENESS_STR_MAP[remoteness_str]
            return Remoteness.UNKNOWN

        elif parameter == JobField.WAGE:
            # Extract wage from the div with class="salary-snippet-container"
            wage_element = soup.find("div", class_="salary-snippet-container")
            if wage_element:
                return wage_element.get_text(strip=True)
            return ""

        elif parameter == JobField.POST_DATE:
            self.logger.debug(f"Soup: {soup}")

            # Extract the post date from span with data-testid="myJobsStateDate"
            post_date_element = soup.find(
                "span", attrs={"data-testid": "myJobsStateDate"}
            )
            self.logger.debug(f"post_date_element: {post_date_element}")
            if post_date_element:
                return calc_post_date_from_relative_str(
                    post_date_element.get_text(strip=True)
                )
            return None

        elif parameter == JobField.KEY_ID:
            # Extract job key (jk) from the anchor tag with "data-jk"
            key_element = soup.find("a", attrs={"data-jk": True})
            if key_element:
                return key_element["data-jk"]
            return None

        else:
            raise NotImplementedError(f"Cannot get {parameter.name}")

    # def get(self, parameter: JobField, soup: BeautifulSoup) -> Any:
    #     """Get a single job attribute from a soup object by JobField"""
    #     if parameter == JobField.TITLE:
    #         return soup.find("a", attrs={"data-tn-element": "jobTitle"}).text.strip()
    #     elif parameter == JobField.COMPANY:
    #         return soup.find("span", attrs={"class": "company"}).text.strip()
    #     elif parameter == JobField.LOCATION:
    #         return soup.find("span", attrs={"class": "location"}).text.strip()
    #     elif parameter == JobField.TAGS:
    #         # tags may not be on page and that's ok.
    #         table_soup = soup.find("table", attrs={"class": "jobCardShelfContainer"})
    #         if table_soup:
    #             return [
    #                 td.text.strip()
    #                 for td in table_soup.find_all(
    #                     "td", attrs={"class": "jobCardShelfItem"}
    #                 )
    #             ]
    #         else:
    #             return []
    #     elif parameter == JobField.REMOTENESS:
    #         remote_field = soup.find("span", attrs={"class": "remote"})
    #         if remote_field:
    #             remoteness_str = remote_field.text.strip().lower()
    #             if remoteness_str in REMOTENESS_STR_MAP:
    #                 return REMOTENESS_STR_MAP[remoteness_str]
    #         return Remoteness.UNKNOWN
    #     elif parameter == JobField.WAGE:
    #         # We may not be able to obtain a wage
    #         potential = soup.find("span", attrs={"class": "salaryText"})
    #         if potential:
    #             return potential.text.strip()
    #         else:
    #             return ""
    #     elif parameter == JobField.POST_DATE:
    #         return calc_post_date_from_relative_str(
    #             soup.find("span", attrs={"class": "date"}).text.strip()
    #         )
    #     elif parameter == JobField.KEY_ID:
    #         return ID_REGEX.findall(
    #             str(soup.find("a", attrs={"class": "sl resultLink save-job-link"}))
    #         )[0]
    #     else:
    #         raise NotImplementedError(f"Cannot get {parameter.name}")

    def set(self, parameter: JobField, job: Job, soup: BeautifulSoup) -> None:
        """Set a single job attribute from a soup object by JobField
        NOTE: URL is high-priority, since we need it to get RAW.
        """
        if parameter == JobField.RAW:

            driver = get_webdriver()

            try:
                # driver.get(job.url)
                driver.uc_open_with_reconnect(job.url)
                # driver.uc_gui_click_captcha()
                # driver.uc_gui_handle_captcha()

                driver.assert_element("div.jobsearch-BodyContainer", timeout=20)
                page_source = driver.page_source

                job._raw_scrape_data = BeautifulSoup(
                    page_source, self.config.bs4_parser
                )
            finally:
                driver.quit()

            # job._raw_scrape_data = BeautifulSoup(
            #     self.session.get(job.url).text, self.config.bs4_parser
            # )
        elif parameter == JobField.DESCRIPTION:
            assert job._raw_scrape_data
            job.description = job._raw_scrape_data.find(
                id="jobDescriptionText"
            ).text.strip()
        elif parameter == JobField.REMOTENESS:
            assert job._raw_scrape_data

            if job.description:
                remoteness = BaseIndeedScraper.checkForRemoteness(job.description)
            else:
                location_text = job._raw_scrape_data.find(
                    id="jobLocationText"
                ).text.strip()
                if location_text:
                    remoteness = BaseIndeedScraper.checkForRemoteness(location_text)
                    job.remoteness = remoteness
        elif parameter == JobField.URL:
            assert job.key_id
            job.url = (
                f"http://www.indeed.{self.config.search_config.domain}/"
                f"viewjob?jk={job.key_id}"
            )
        else:
            raise NotImplementedError(f"Cannot set {parameter.name}")

    def _get_search_url(self, method: Optional[str] = "get") -> str:
        """Get the indeed search url from SearchTerms
        TODO: use Enum for method instead of str.
        """
        if method == "get":
            return (
                "https://www.indeed.{}/jobs?q={}&l={}%2C+{}&radius={}&"
                "limit={}&filter={}{}".format(
                    self.config.search_config.domain,
                    self.query,
                    self.config.search_config.city.replace(
                        " ",
                        "+",
                    ),
                    self.config.search_config.province_or_state.upper(),
                    self._quantize_radius(self.config.search_config.radius),
                    self.max_results_per_page,
                    int(self.config.search_config.return_similar_results),
                    REMOTENESS_TO_QUERY[self.config.search_config.remoteness],
                )
            )
        elif method == "post":
            raise NotImplementedError()
        else:
            raise ValueError(f"No html method {method} exists")

    def _quantize_radius(self, radius: int) -> int:
        """Quantizes the user input radius to a valid radius value into:
        5, 10, 15, 25, 50, 100, and 200 kilometers or miles.
        TODO: implement with numpy instead of if/else cases.
        """
        if radius < 5:
            radius = 0
        elif 5 <= radius < 10:
            radius = 5
        elif 10 <= radius < 15:
            radius = 10
        elif 15 <= radius < 25:
            radius = 15
        elif 25 <= radius < 50:
            radius = 25
        elif 50 <= radius < 100:
            radius = 50
        elif radius >= 100:
            radius = 100
        return radius

    def _get_job_soups_from_search_page(
        self, search: str, page: str, job_soup_list: List[BeautifulSoup]
    ) -> None:
        """Scrapes the indeed page for a list of job soups
        NOTE: modifies the job_soup_list in-place
        NOTE: Indeed's remoteness filter sucks, and we will always see a mix.
            ... need to add some kind of filtering for this!
        """
        driver = get_webdriver()

        url = f"{search}&start={int(page * self.max_results_per_page)}"

        try:
            # driver.get(url)
            driver.uc_open_with_reconnect(url)
            # driver.uc_gui_click_captcha()
            # driver.uc_gui_handle_captcha()
            driver.assert_element("div.jobsearch-RightPane", timeout=20)

            page_source = driver.page_source
        finally:
            driver.quit()
        soup = BeautifulSoup(page_source, self.config.bs4_parser)

        job_soup_list.extend(soup.find_all("div", class_="job_seen_beacon"))

        # job_soup_list.extend(
        #     soup.find_all("div", attrs={"data-tn-component": "organicJob"})
        # )

        # job_soup_list.extend(
        #     BeautifulSoup(self.session.get(url).text, self.config.bs4_parser).find_all(
        #         "div", attrs={"data-tn-component": "organicJob"}
        #     )
        # )

    def _get_num_search_result_pages(self, search_url: str, max_pages=0) -> int:
        """Calculates the number of pages of job listings to be scraped.

        i.e. your search yields 230 results at 50 res/page -> 5 pages of jobs

        Args:
                        max_pages: the maximum number of pages to be scraped.
        Returns:
            The number of pages to be scraped.
        """
        # Get the html data, initialize bs4 with lxml
        # request_html = self.session.get(search_url)

        driver = get_webdriver()

        try:
            # driver.get(search_url)
            driver.uc_open_with_reconnect(search_url)
            # driver.uc_gui_click_captcha()
            # driver.uc_gui_handle_captcha()
            driver.assert_element("div.jobsearch-RightPane", timeout=20)
            page_source = driver.page_source

            # self.logger.debug(page_source)
        finally:
            driver.quit()

        # self.logger.debug("request_text: %s", request_html.text)
        # self.logger.debug("Got Base search results page: %s", search_url)
        query_resp = BeautifulSoup(page_source, self.config.bs4_parser)

        # self.logger.debug(query_resp)

        job_count_element = query_resp.find(
            "div", class_="jobsearch-JobCountAndSortPane-jobCount"
        )

        # self.logger.debug("job_count_element: %s", job_count_element)
        # self.logger.debug(job_count_element)

        if not job_count_element:
            raise ValueError(
                "Unable to identify number of pages of results for query: {}. "
                "Please ensure the page contains results.".format(search_url)
            )

        # Extract the text from the <span> inside the job count div
        job_count_text = job_count_element.find("span").get_text(strip=True)

        # self.logger.debug("Job Count Text", job_count_text)

        # Extract the number of jobs from the text (e.g., "100+ jobs" -> 100)
        job_count_match = re.search(r"(\d+)[\+]* jobs", job_count_text)

        if not job_count_match:
            raise ValueError(f"Could not extract job count from text: {job_count_text}")

        num_res = int(job_count_match.group(1))  # Extract the number

        self.logger.debug("Found %d jobs", num_res)

        # Calculate the number of pages based on the number of results and the max results per page
        number_of_pages = int(ceil(num_res / self.max_results_per_page))

        if max_pages == 0:
            return number_of_pages
        elif number_of_pages < max_pages:
            return number_of_pages
        else:
            return max_pages

        # num_res = query_resp.find(id="searchCountPages")
        # # TODO: we should consider expanding the error cases (scrape error page)
        # if not num_res:
        #     raise ValueError(
        #         "Unable to identify number of pages of results for query: {}"
        #         " Please ensure linked page contains results, you may have"
        #         " provided a city for which there are no results within this"
        #         " province or state.".format(search_url)
        #     )

        # num_res = num_res.contents[0].strip()
        # num_res = int(re.findall(r"f (\d+) ", num_res.replace(",", ""))[0])
        # number_of_pages = int(ceil(num_res / self.max_results_per_page))
        # if max_pages == 0:
        #     return number_of_pages
        # elif number_of_pages < max_pages:
        #     return number_of_pages
        # else:
        #     return max_pages


class IndeedScraperCANEng(BaseIndeedScraper, BaseCANEngScraper):
    """Scrapes jobs from www.indeed.ca"""


class IndeedScraperUSAEng(BaseIndeedScraper, BaseUSAEngScraper):
    """Scrapes jobs from www.indeed.com"""


class IndeedScraperUKEng(BaseIndeedScraper, BaseUKEngScraper):
    """Scrapes jobs from www.indeed.co.uk"""

    def _get_search_url(self, method: Optional[str] = "get") -> str:
        """Get the indeed search url from SearchTerms
        TODO: use Enum for method instead of str.
        """
        if method == "get":
            return (
                "https://www.indeed.{}/jobs?q={}&l={}&radius={}&"
                "limit={}&filter={}{}".format(
                    self.config.search_config.domain,
                    self.query,
                    self.config.search_config.city.replace(
                        " ",
                        "+",
                    ),
                    self._quantize_radius(self.config.search_config.radius),
                    self.max_results_per_page,
                    int(self.config.search_config.return_similar_results),
                    REMOTENESS_TO_QUERY[self.config.search_config.remoteness],
                )
            )
        elif method == "post":
            raise NotImplementedError()
        else:
            raise ValueError(f"No html method {method} exists")


class IndeedScraperFRFre(BaseIndeedScraper, BaseFRFreScraper):
    """Scrapes jobs from www.indeed.fr"""

    def _get_search_url(self, method: Optional[str] = "get") -> str:
        """Get the indeed search url from SearchTerms
        TODO: use Enum for method instead of str.
        """
        if method == "get":
            return (
                "https://www.indeed.{}/jobs?q={}&l={}+%28{}%29&radius={}&"
                "limit={}&filter={}{}".format(
                    self.config.search_config.domain,
                    self.query,
                    self.config.search_config.city.replace(
                        " ",
                        "+",
                    ),
                    self.config.search_config.province_or_state.upper(),
                    self._quantize_radius(self.config.search_config.radius),
                    self.max_results_per_page,
                    int(self.config.search_config.return_similar_results),
                    REMOTENESS_TO_QUERY[self.config.search_config.remoteness],
                )
            )
        elif method == "post":
            raise NotImplementedError()
        else:
            raise ValueError(f"No html method {method} exists")

    def _get_num_search_result_pages(self, search_url: str, max_pages=0) -> int:
        """Calculates the number of pages of job listings to be scraped.

        i.e. your search yields 230 results at 50 res/page -> 5 pages of jobs

        Args:
                        max_pages: the maximum number of pages to be scraped.
        Returns:
            The number of pages to be scraped.
        """
        # Get the html data, initialize bs4 with lxml
        request_html = self.session.get(search_url)
        self.logger.debug("Got Base search results page: %s", search_url)
        query_resp = BeautifulSoup(request_html.text, self.config.bs4_parser)
        num_res = query_resp.find(id="searchCountPages")
        # TODO: we should consider expanding the error cases (scrape error page)
        if not num_res:
            raise ValueError(
                "Unable to identify number of pages of results for query: {}"
                " Please ensure linked page contains results, you may have"
                " provided a city for which there are no results within this"
                " province or state.".format(search_url)
            )

        num_res = normalize("NFKD", num_res.contents[0].strip())
        num_res = int(re.findall(r"(\d+) ", num_res.replace(",", ""))[1])
        number_of_pages = int(ceil(num_res / self.max_results_per_page))
        if max_pages == 0:
            return number_of_pages
        elif number_of_pages < max_pages:
            return number_of_pages
        else:
            return max_pages


class IndeedScraperDEGer(BaseIndeedScraper, BaseDEGerScraper):
    """Scrapes jobs from de.indeed.com"""

    # The german locale has a different number separators.
    THOUSEP = "."

    def _get_search_url(self, method: Optional[str] = "get") -> str:
        """Get the indeed search url from SearchTerms"""
        if method == "get":
            return (
                # The URL is different to the base scraper because indeed.de is
                # redirecting to de.indeed.com. If the redirect is handled the
                # same URLs can be used.
                "https://{}.indeed.com/jobs?q={}&l={}&radius={}&"
                "limit={}&filter={}{}".format(
                    self.config.search_config.domain,
                    self.query,
                    self.config.search_config.city.replace(
                        " ",
                        "+",
                    ),
                    self._quantize_radius(self.config.search_config.radius),
                    self.max_results_per_page,
                    int(self.config.search_config.return_similar_results),
                    REMOTENESS_TO_QUERY[self.config.search_config.remoteness],
                )
            )
        elif method == "post":
            raise NotImplementedError()
        else:
            raise ValueError(f"No html method {method} exists")

    def _get_num_search_result_pages(self, search_url: str, max_pages=0) -> int:
        """Calculates the number of pages of job listings to be scraped.

        i.e. your search yields 230 results at 50 res/page -> 5 pages of jobs

        Args:
            max_pages: the maximum number of pages to be scraped.
        Returns:
            The number of pages to be scraped.
        """
        # Get the html data, initialize bs4 with lxml
        request_html = self.session.get(search_url)
        self.logger.debug("Got Base search results page: %s", search_url)
        query_resp = BeautifulSoup(request_html.text, self.config.bs4_parser)
        num_res = query_resp.find(id="searchCountPages")
        if not num_res:
            raise ValueError(
                "Unable to identify number of pages of results for query: {}"
                " Please ensure linked page contains results, you may have"
                " provided a city for which there are no results within this"
                " province or state.".format(search_url)
            )

        num_res = num_res.contents[0].strip()
        num_res = int(re.findall(r"(\d+)", num_res.replace(self.THOUSEP, ""))[1])
        number_of_pages = int(ceil(num_res / self.max_results_per_page))
        if max_pages == 0:
            return number_of_pages
        elif number_of_pages < max_pages:
            return number_of_pages
        else:
            return max_pages
