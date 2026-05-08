from .generic import HtmlSearchTemplateConnector, JsonApiConnector, XmlFeedConnector
from .marketplace import MarketplaceTemplateConnector


class ConnectorFactory:
    CONNECTOR_MAP = {
        "json_api": JsonApiConnector,
        "xml_feed": XmlFeedConnector,
        "html_search_template": HtmlSearchTemplateConnector,
        "marketplace_template": MarketplaceTemplateConnector,
    }

    @classmethod
    def build(cls, integration):
        connector_class = cls.CONNECTOR_MAP.get(integration.connector_type)
        if connector_class is None:
            raise ValueError(f"Unsupported connector type: {integration.connector_type}")
        return connector_class(integration)
