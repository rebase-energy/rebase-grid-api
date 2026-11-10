"""Python client for the Rebase Grid API: European power system data, no API key needed."""
from rebase_grid_api.client import BASE_URL, GridAPI, GridAPIError

__all__ = ["GridAPI", "GridAPIError", "BASE_URL"]
__version__ = "0.1.0"
