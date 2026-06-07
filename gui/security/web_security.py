"""
AGREGAT GUI — QWebEngineView Security Configuration

M-01 FIX: Content Security Policy and security hardening for embedded web views.

Security model:
- Disable all unnecessary web features (geolocation, clipboard, etc.)
- Block remote URL access from local content
- Block file URL access from web content
- Disable insecure content loading
- Restrict JavaScript capabilities
- No external resource loading

Used by:
- AI editor web view
- Strategy editor web view
- Report preview web view
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("StrategyTester.gui.security.web")

try:
    from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtCore import QUrl
    _PYSIDE_AVAILABLE = True
except ImportError:
    _PYSIDE_AVAILABLE = False
    logger.warning("PySide6 not available — web security configuration skipped")


# ===================================================================
# Web Security Configuration
# ===================================================================

class WebSecurityConfig:
    """
    Security configuration for QWebEngineView instances.
    
    Provides pre-configured security profiles:
    - STRICT: Maximum security (for displaying untrusted content)
    - STANDARD: Balanced security (for AI editor, strategy editor)
    - RELAXED: Minimum security (for trusted local reports)
    """
    
    STRICT = "strict"
    STANDARD = "standard"
    RELAXED = "relaxed"
    
    PROFILES = {
        STRICT: {
            "JavascriptEnabled": False,
            "JavascriptCanOpenWindows": False,
            "JavascriptCanAccessClipboard": False,
            "LocalContentCanAccessRemoteUrls": False,
            "LocalContentCanAccessFileUrls": False,
            "AllowRunningInsecureContent": False,
            "AllowGeolocationOnInsecureOrigins": False,
            "DnsPrefetchEnabled": False,
            "PluginsEnabled": False,
            "WebGLEnabled": False,
            "Accelerated2dCanvasEnabled": False,
            "AutoLoadIconsForPage": False,
            "TouchIconsEnabled": False,
            "PdfViewerEnabled": False,
        },
        STANDARD: {
            "JavascriptEnabled": True,  # Needed for code editors
            "JavascriptCanOpenWindows": False,
            "JavascriptCanAccessClipboard": False,
            "LocalContentCanAccessRemoteUrls": False,
            "LocalContentCanAccessFileUrls": False,
            "AllowRunningInsecureContent": False,
            "AllowGeolocationOnInsecureOrigins": False,
            "DnsPrefetchEnabled": False,
            "PluginsEnabled": False,
            "WebGLEnabled": False,
            "Accelerated2dCanvasEnabled": True,  # For code editor rendering
            "AutoLoadIconsForPage": False,
            "TouchIconsEnabled": False,
            "PdfViewerEnabled": False,
        },
        RELAXED: {
            "JavascriptEnabled": True,
            "JavascriptCanOpenWindows": False,
            "JavascriptCanAccessClipboard": False,
            "LocalContentCanAccessRemoteUrls": False,
            "LocalContentCanAccessFileUrls": True,  # For local report assets
            "AllowRunningInsecureContent": False,
            "AllowGeolocationOnInsecureOrigins": False,
            "DnsPrefetchEnabled": False,
            "PluginsEnabled": False,
            "WebGLEnabled": True,  # For report charts
            "Accelerated2dCanvasEnabled": True,
            "AutoLoadIconsForPage": False,
            "TouchIconsEnabled": False,
            "PdfViewerEnabled": True,
        },
    }


def configure_web_security(
    web_view,
    profile: str = WebSecurityConfig.STANDARD,
    custom_settings: Optional[dict] = None,
) -> None:
    """
    Apply security configuration to a QWebEngineView.
    
    Args:
        web_view: QWebEngineView instance
        profile: Security profile ('strict', 'standard', 'relaxed')
        custom_settings: Override specific settings
    
    Usage:
        web_view = QWebEngineView()
        configure_web_security(web_view, profile='strict')
    """
    if not _PYSIDE_AVAILABLE:
        return
    
    settings = web_view.settings()
    
    # Get profile settings
    profile_settings = WebSecurityConfig.PROFILES.get(
        profile, WebSecurityConfig.PROFILES[WebSecurityConfig.STANDARD]
    )
    
    # Apply settings
    for attr_name, value in profile_settings.items():
        attr = getattr(QWebEngineSettings.WebAttribute, attr_name, None)
        if attr is not None:
            settings.setAttribute(attr, value)
        else:
            logger.debug("Unknown web setting: %s", attr_name)
    
    # Apply custom overrides
    if custom_settings:
        for attr_name, value in custom_settings.items():
            attr = getattr(QWebEngineSettings.WebAttribute, attr_name, None)
            if attr is not None:
                settings.setAttribute(attr, value)
    
    # === Additional security measures ===
    
    # Set a restrictive user agent (don't leak app info)
    profile_obj = web_view.page().profile()
    profile_obj.setHttpUserAgent(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    )
    
    # Disable HTTP cache (prevent sensitive data caching)
    profile_obj.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
    
    # Clear cookies on startup
    cookie_store = profile_obj.cookieStore()
    cookie_store.deleteAllCookies()
    
    # Set persistent storage to isolated location
    # (prevent cross-contamination between web views)
    
    logger.info("Web security configured: profile=%s", profile)


def create_isolated_web_view(
    profile: str = WebSecurityConfig.STANDARD,
    parent=None,
) -> "QWebEngineView":
    """
    Create a new QWebEngineView with security pre-configured.
    
    Uses an isolated profile (separate cookies, cache, storage).
    
    Returns:
        Configured QWebEngineView instance
    """
    if not _PYSIDE_AVAILABLE:
        raise RuntimeError("PySide6 not available")
    
    # Create isolated profile
    import uuid
    profile_name = f"agregat_isolated_{uuid.uuid4().hex[:8]}"
    isolated_profile = QWebEngineProfile(profile_name)
    
    # No persistent storage
    isolated_profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
    )
    isolated_profile.setHttpCacheType(
        QWebEngineProfile.HttpCacheType.NoCache
    )
    
    # Create web view with isolated profile
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEnginePage
    
    web_view = QWebEngineView(parent)
    page = QWebEnginePage(isolated_profile, web_view)
    web_view.setPage(page)
    
    # Apply security settings
    configure_web_security(web_view, profile=profile)
    
    return web_view


def set_csp_headers_for_local_content(html_content: str) -> str:
    """
    Inject CSP meta tag into local HTML content.
    
    For web views loading local HTML (reports, editors),
    this adds a Content-Security-Policy meta tag.
    
    Args:
        html_content: Raw HTML string
    
    Returns:
        HTML with CSP meta tag injected
    """
    csp_meta = (
        '<meta http-equiv="Content-Security-Policy" '
        'content="default-src \'self\' \'unsafe-inline\' data:; '
        'script-src \'self\' \'unsafe-inline\'; '
        'style-src \'self\' \'unsafe-inline\'; '
        'img-src \'self\' data:; '
        'connect-src \'none\'; '
        'frame-src \'none\'; '
        'object-src \'none\'; '
        'base-uri \'self\'; '
        'form-action \'none\';">'
    )
    
    # Inject into <head>
    if "<head>" in html_content.lower():
        return html_content.replace(
            "<head>", f"<head>\n{csp_meta}", 1
        )
    elif "<html>" in html_content.lower():
        return html_content.replace(
            "<html>", f"<html>\n<head>{csp_meta}</head>", 1
        )
    else:
        return f"<html><head>{csp_meta}</head><body>{html_content}</body></html>"


__all__ = [
    'WebSecurityConfig',
    'configure_web_security',
    'create_isolated_web_view',
    'set_csp_headers_for_local_content',
]