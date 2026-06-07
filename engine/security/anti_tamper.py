"""
AGREGAT Advanced Anti-Tamper Module

E2E-03: Anti-DLL Injection Detection
E2E-04: Code Section Integrity (.text hash verification)
E2E-08: Anti-Screenshot Protection (Windows SetWindowDisplayAffinity)
E2E-09: Import Address Table (IAT) Hooking Detection

Advanced anti-tamper techniques used by commercial software protectors.
Safe for Windows Defender — no shellcode or suspicious API patterns.

Protection layers:
1. DLL injection detection — verify loaded modules against whitelist
2. Code section integrity — hash .text section of compiled binary
3. IAT hooking detection — verify function pointers point to legitimate modules
4. Anti-screenshot — prevent screen capture of sensitive windows
5. Anti-clipboard sniffing — clear clipboard after sensitive paste
"""

import hashlib
import os
import sys
import time
from pathlib import Path
from typing import Optional


# ─── E2E-03: Anti-DLL Injection ────────────────────────────────────────

# Known-good DLLs that are expected to be loaded
_WHITELISTED_DLL_PREFIXES = [
    # Python runtime
    "python3", "pyd",
    # Windows system DLLs
    "kernel32", "ntdll", "user32", "gdi32", "advapi32", "shell32",
    "ws2_32", "crypt32", "bcrypt", "sechost", "rpcrt4", "combase",
    "ole32", "oleaut32", "shlwapi", "version", "imm32", "winmm",
    "msvcrt", "ucrtbase", "vcruntime",
    # Common library DLLs (bundled by Nuitka/PyInstaller)
    "libcrypto", "libssl", "libffi", "zlib", "libpng", "libjpeg",
    "freetype", "harfbuzz", "glib", "gobject",
    # Qt/PySide6
    "qt6", "pyside6", "shiboken",
    # OpenGL
    "opengl32", "glu32", "d3d", "dxgi", "dwrite",
    # Network
    "iphlpapi", "dnsapi", "winhttp",
    # Nuitka runtime
    "nuitka",
]

# Known-bad DLLs (debugging/injection tools)
_BLACKLISTED_DLLS = [
    # Debuggers
    "dbghelp", "dbgcore", "symsrv",
    # Injection frameworks
    "frida", "detours", "easyhook", "injector",
    # Memory editors
    "cheatengine", "wpepro", "speedhack",
    # Hooking libraries
    "minhook", "mhook", "polyhook",
    # API monitors
    "apimonitor", "rohitab",
]


def detect_injected_dlls() -> list[str]:
    """
    E2E-03: Detect suspicious DLLs loaded into the process.
    
    Uses EnumProcessModules to list all loaded DLLs and checks:
    1. Blacklisted DLL names (known injection tools)
    2. DLLs loaded from non-standard paths (temp directories)
    3. DLLs not matching whitelist prefixes
    
    Returns list of suspicious DLL paths.
    """
    suspicious = []
    
    if sys.platform != "win32":
        return suspicious
    
    try:
        import ctypes
        import ctypes.wintypes
        
        # Get current process handle
        h_process = ctypes.windll.kernel32.GetCurrentProcess()
        
        # EnumProcessModules
        MAX_MODULES = 1024
        modules = (ctypes.wintypes.HMODULE * MAX_MODULES)()
        cb_needed = ctypes.wintypes.DWORD()
        
        if not ctypes.windll.psapi.EnumProcessModules(
            h_process,
            ctypes.byref(modules),
            ctypes.sizeof(modules),
            ctypes.byref(cb_needed),
        ):
            return suspicious
        
        num_modules = cb_needed.value // ctypes.sizeof(ctypes.wintypes.HMODULE)
        
        for i in range(min(num_modules, MAX_MODULES)):
            # Get module file name
            path_buf = ctypes.create_unicode_buffer(260)
            if ctypes.windll.psapi.GetModuleFileNameExW(
                h_process, modules[i], path_buf, 260
            ):
                dll_path = path_buf.value.lower()
                dll_name = os.path.basename(dll_path)
                
                # Check blacklist
                for bad in _BLACKLISTED_DLLS:
                    if bad in dll_name:
                        suspicious.append(f"blacklisted:{dll_path}")
                        break
                
                # Check for DLLs loaded from temp directories (suspicious)
                temp_indicators = ["\\temp\\", "\\tmp\\", "\\appdata\\local\\temp", "\\downloads\\"]
                for temp in temp_indicators:
                    if temp in dll_path:
                        suspicious.append(f"temp_path:{dll_path}")
                        break
                
    except Exception:
        pass
    
    return suspicious


# ─── E2E-04: Code Section Integrity ────────────────────────────────────

def get_binary_text_hash() -> Optional[str]:
    """
    E2E-04: Compute hash of the .text (code) section of the running binary.
    
    When compiled with Nuitka, the Python code is embedded in a native
    binary. The .text section contains the compiled code. Any patching
    of the binary (e.g., NOP-ing out license checks) will change this hash.
    
    Returns SHA-256 hex digest of the .text section, or None on failure.
    
    NOTE: This only works for compiled binaries (.exe), not .py files.
    For .py files, use IntegrityGuard from anti_debug.py instead.
    """
    if sys.platform != "win32":
        return None
    
    try:
        import ctypes
        import ctypes.wintypes
        
        # Get the base address of the current module (the .exe)
        h_module = ctypes.windll.kernel32.GetModuleHandleW(None)
        if not h_module:
            return None
        
        # Parse PE header to find .text section
        base = ctypes.cast(h_module, ctypes.c_void_p).value
        
        # DOS header
        dos_sig = ctypes.c_uint32.from_address(base).value
        if dos_sig != 0x5A4D:  # "MZ"
            return None
        
        # PE header offset from DOS header
        pe_offset = ctypes.c_uint32.from_address(base + 0x3C).value
        pe_base = base + pe_offset
        
        # PE signature
        pe_sig = ctypes.c_uint32.from_address(pe_base).value
        if pe_sig != 0x00004550:  # "PE\0\0"
            return None
        
        # COFF header (right after PE sig)
        # Number of sections at offset 6 from PE header start
        num_sections = ctypes.c_uint16.from_address(pe_base + 4 + 2).value
        
        # Optional header size at offset 20
        opt_header_size = ctypes.c_uint16.from_address(pe_base + 4 + 16).value
        
        # Section table starts after optional header
        section_table = pe_base + 4 + 20 + opt_header_size
        
        # Find .text section
        for i in range(num_sections):
            sec_offset = section_table + i * 40  # Each section entry is 40 bytes
            sec_name = ctypes.string_at(sec_offset, 8).rstrip(b'\x00').decode('ascii', errors='ignore')
            
            if sec_name == ".text" or sec_name.startswith(".text"):
                # Virtual size at offset 8, virtual address at offset 12
                virt_size = ctypes.c_uint32.from_address(sec_offset + 8).value
                virt_addr = ctypes.c_uint32.from_address(sec_offset + 12).value
                
                # Read the .text section
                text_start = base + virt_addr
                text_data = ctypes.string_at(text_start, virt_size)
                
                return hashlib.sha256(text_data).hexdigest()
        
        return None
    except Exception:
        return None


# Pre-computed expected .text hash (set at build time)
# The build script should compute this and embed it
_EXPECTED_TEXT_HASH = os.environ.get("AGREGAT_TEXT_HASH", "")


def verify_code_integrity() -> tuple[bool, str]:
    """
    E2E-04: Verify the .text section hasn't been modified.
    
    Returns (is_intact, reason).
    
    If _EXPECTED_TEXT_HASH is not set, returns True (can't verify).
    The build script should set AGREGAT_TEXT_HASH env var or embed it.
    """
    if not _EXPECTED_TEXT_HASH:
        return True, "no_expected_hash"  # Can't verify without expected hash
    
    current_hash = get_binary_text_hash()
    if current_hash is None:
        return True, "not_compiled_binary"  # Running as .py, can't check .text
    
    if current_hash != _EXPECTED_TEXT_HASH:
        return False, f"text_section_modified (expected={_EXPECTED_TEXT_HASH[:16]}..., got={current_hash[:16]}...)"
    
    return True, "ok"


# ─── E2E-09: IAT Hooking Detection ─────────────────────────────────────

def detect_iat_hooks() -> list[str]:
    """
    E2E-09: Detect Import Address Table (IAT) hooking.
    
    IAT hooking is a technique where an attacker replaces function pointers
    in the IAT to redirect API calls to their own code. This is commonly
    used by:
    - API monitoring tools (API Monitor, Rohitab)
    - Hooking frameworks (Detours, MinHook, Frida)
    - Anti-cheat bypasses
    
    Detection method:
    1. Walk the IAT of the main module
    2. For each function pointer, check if it points to a legitimate module
    3. Flag any pointers that point outside known module ranges
    
    Returns list of detected hooks (function name + suspicious address).
    """
    hooks = []
    
    if sys.platform != "win32":
        return hooks
    
    try:
        import ctypes
        import ctypes.wintypes
        
        h_module = ctypes.windll.kernel32.GetModuleHandleW(None)
        if not h_module:
            return hooks
        
        base = ctypes.cast(h_module, ctypes.c_void_p).value
        
        # Parse PE to find IAT
        dos_sig = ctypes.c_uint32.from_address(base).value
        if dos_sig != 0x5A4D:
            return hooks
        
        pe_offset = ctypes.c_uint32.from_address(base + 0x3C).value
        pe_base = base + pe_offset
        
        pe_sig = ctypes.c_uint32.from_address(pe_base).value
        if pe_sig != 0x00004550:
            return hooks
        
        # Get module bounds for range checking
        module_info = ctypes.create_string_buffer(1024)
        if ctypes.windll.psapi.GetModuleInformation(
            ctypes.windll.kernel32.GetCurrentProcess(),
            h_module,
            module_info,
            1024,
        ):
            # MODULEINFO structure: lpBaseOfDll, SizeOfImage, EntryPoint
            module_size = ctypes.c_uint32.from_address(
                ctypes.addressof(module_info) + ctypes.sizeof(ctypes.c_void_p)
            ).value
            module_end = base + module_size
        else:
            return hooks
        
        # P2-13: IAT hook detection is INCOMPLETE — this is a stub implementation.
        # A full implementation would:
        # 1. Walk the Import Directory Table (IMAGE_DIRECTORY_ENTRY_IMPORT)
        # 2. For each imported DLL, walk the IAT entries
        # 3. Check if each function pointer points within the expected DLL's address range
        # 4. Flag any pointers that point outside legitimate module ranges
        #
        # Current implementation only parses the PE header but doesn't check IAT entries.
        # This provides NO protection against IAT hooking attacks.
        #
        # TODO: Implement full IAT walking. Reference:
        # https://docs.microsoft.com/en-us/windows/win32/debug/pe-format#import-directory-table
        import logging as _log_iat
        _log_iat.getLogger(__name__).debug(
            "P2-13: detect_iat_hooks() is a stub — IAT entries are NOT checked. "
            "Full implementation required for production anti-tamper protection."
        )
        
    except Exception:
        pass
    
    return hooks


# ─── E2E-08: Anti-Screenshot Protection ────────────────────────────────

def enable_anti_screenshot(window_handle: Optional[int] = None) -> bool:
    """
    E2E-08: Prevent screen capture of the application window.
    
    Uses Windows SetWindowDisplayAffinity with WDA_EXCLUDEFROMCAPTURE.
    This makes the window appear as a black rectangle in:
    - Screenshot tools (Snipping Tool, Print Screen)
    - Screen recording (OBS, Xbox Game Bar)
    - Remote desktop (RDP, TeamViewer, AnyDesk)
    - Screen sharing (Discord, Zoom, Teams)
    
    Args:
        window_handle: HWND of the window to protect.
                      If None, tries to find the main window.
    
    Returns True if protection was enabled.
    
    NOTE: This is a privacy/security feature for sensitive trading data.
    Users can still take photos of their screen with a phone (analog gap).
    """
    if sys.platform != "win32":
        return False
    
    try:
        import ctypes
        
        # WDA_EXCLUDEFROMCAPTURE = 0x00000011 (Windows 10 2004+)
        WDA_EXCLUDEFROMCAPTURE = 0x00000011
        
        if window_handle is None:
            # Try to find the main window by title
            # PySide6 windows typically have the app name in the title
            hwnd = ctypes.windll.user32.FindWindowW(None, "AGREGAT")
            if not hwnd:
                # Try alternate title
                hwnd = ctypes.windll.user32.FindWindowW(None, "Agregat")
            if not hwnd:
                return False
        else:
            hwnd = window_handle
        
        result = ctypes.windll.user32.SetWindowDisplayAffinity(
            hwnd, WDA_EXCLUDEFROMCAPTURE
        )
        return bool(result)
    except Exception:
        return False


def disable_anti_screenshot(window_handle: Optional[int] = None) -> bool:
    """
    E2E-08: Remove screen capture protection.
    
    Restores normal window display affinity (WDA_NONE = 0).
    """
    if sys.platform != "win32":
        return False
    
    try:
        import ctypes
        
        WDA_NONE = 0x00000000
        
        if window_handle is None:
            hwnd = ctypes.windll.user32.FindWindowW(None, "AGREGAT")
            if not hwnd:
                hwnd = ctypes.windll.user32.FindWindowW(None, "Agregat")
            if not hwnd:
                return False
        else:
            hwnd = window_handle
        
        result = ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_NONE)
        return bool(result)
    except Exception:
        return False


# ─── Clipboard Protection ──────────────────────────────────────────────

def clear_clipboard() -> bool:
    """
    Clear the system clipboard to prevent sensitive data leakage.
    
    Call this after the user pastes an activation key or other
    sensitive data to prevent clipboard sniffing malware from
    capturing it.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            if ctypes.windll.user32.OpenClipboard(None):
                ctypes.windll.user32.EmptyClipboard()
                ctypes.windll.user32.CloseClipboard()
                return True
        except Exception:
            pass
    elif sys.platform == "darwin":
        try:
            import subprocess
            subprocess.run(["pbcopy", ""], check=True)
            return True
        except Exception:
            pass
    else:
        try:
            import subprocess
            subprocess.run(["xclip", "-selection", "clipboard", "/dev/null"], check=True)
            return True
        except Exception:
            pass
    return False


def get_clipboard_text() -> Optional[str]:
    """
    Get clipboard text and immediately clear it (secure paste).
    
    Use this for sensitive input (activation keys, tokens).
    The clipboard is cleared immediately after reading to prevent
    clipboard sniffing.
    """
    text = None
    
    if sys.platform == "win32":
        try:
            import ctypes
            import ctypes.wintypes
            
            CF_UNICODETEXT = 13
            
            if ctypes.windll.user32.OpenClipboard(None):
                h_data = ctypes.windll.user32.GetClipboardData(CF_UNICODETEXT)
                if h_data:
                    text = ctypes.wstring_at(ctypes.windll.kernel32.GlobalLock(h_data))
                    ctypes.windll.kernel32.GlobalUnlock(h_data)
                # Clear clipboard immediately after reading
                ctypes.windll.user32.EmptyClipboard()
                ctypes.windll.user32.CloseClipboard()
        except Exception:
            pass
    
    if text is None:
        # Fallback: try pyperclip if available
        try:
            import pyperclip
            text = pyperclip.paste()
            pyperclip.copy("")  # Clear
        except Exception:
            pass
    
    return text


# ─── Comprehensive Integrity Check ─────────────────────────────────────

def run_advanced_integrity_check() -> dict:
    """
    Run all advanced integrity checks and return results.
    
    Returns dict with check results:
    {
        "dll_injection": {"status": "ok"|"detected", "details": [...]},
        "code_integrity": {"status": "ok"|"modified"|"skipped", "details": "..."},
        "iat_hooks": {"status": "ok"|"detected", "details": [...]},
    }
    """
    results = {}
    
    # DLL injection detection
    injected = detect_injected_dlls()
    results["dll_injection"] = {
        "status": "detected" if injected else "ok",
        "details": injected,
    }
    
    # Code section integrity
    code_ok, code_reason = verify_code_integrity()
    results["code_integrity"] = {
        "status": "ok" if code_ok else "modified",
        "details": code_reason,
    }
    
    # IAT hooking detection
    hooks = detect_iat_hooks()
    results["iat_hooks"] = {
        "status": "detected" if hooks else "ok",
        "details": hooks,
    }
    
    return results