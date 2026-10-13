from .confluence import ConfluenceConnector
from .drive import DriveConnector
from .jira import JiraConnector
from .slack import SlackConnector

registry = {'drive': DriveConnector(), 'confluence': ConfluenceConnector(), 'jira': JiraConnector(), 'slack': SlackConnector()}
