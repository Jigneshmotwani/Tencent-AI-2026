from .confluence import ConfluenceConnector
from .drive import DriveConnector

registry = {'drive': DriveConnector(), 'confluence': ConfluenceConnector()}
