# __init__.py

def classFactory(iface):
    """Load MultipointLineNaming class from main.py."""
    from .main import MultipointLineNaming
    return MultipointLineNaming(iface)