# -*- coding: utf-8 -*-

def classFactory(iface):
    """Load MultipointLineNaming class from main.py."""
    from .main import MultipointLineNaming
    return MultipointLineNaming(iface)