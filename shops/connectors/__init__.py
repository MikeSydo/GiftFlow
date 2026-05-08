from .factory import ConnectorFactory


def get_connector(integration):
    return ConnectorFactory.build(integration)


__all__ = ["ConnectorFactory", "get_connector"]
