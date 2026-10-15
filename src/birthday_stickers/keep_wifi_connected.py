"""Windows Wi-Fi watchdog. Run: birthday-keep-wifi

Connect to your desired saved Wi-Fi network before starting. Checks/retries
are separated by 10 seconds. Stop with Ctrl+C. No third-party packages.
Monitors Wi-Fi association, not Internet reachability. Does not prevent sleep.
Uses the Windows WLAN API, independent of Windows display language.
"""

import argparse
import ctypes as C
import sys
import time
from contextlib import contextmanager
from datetime import datetime

DWORD = C.c_uint32
INT = C.c_int32
PTR = C.c_void_p


class GUID(C.Structure):
    _fields_ = [("a", DWORD), ("b", C.c_uint16), ("c", C.c_uint16), ("d", C.c_ubyte * 8)]


class Interface(C.Structure):
    _fields_ = [("guid", GUID), ("description", C.c_wchar * 256), ("state", INT)]


class SSID(C.Structure):
    _fields_ = [("length", DWORD), ("data", C.c_ubyte * 32)]


class ConnectionPrefix(C.Structure):
    # Initial fields of WLAN_CONNECTION_ATTRIBUTES; remaining fields unused.
    _fields_ = [
        ("state", INT),
        ("mode", INT),
        ("profile", C.c_wchar * 256),
        ("ssid", SSID),
        ("bss_type", INT),
    ]


class Parameters(C.Structure):
    _fields_ = [
        ("mode", INT),
        ("profile", C.c_wchar_p),
        ("ssid", C.POINTER(SSID)),
        ("bssid_list", PTR),
        ("bss_type", INT),
        ("flags", DWORD),
    ]


def log(message):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}", flush=True)


def check(code):
    if code:
        if code == 5:
            raise OSError(
                "Wi-Fi access denied. Check Windows Settings > Privacy & "
                "security > Location, including access for desktop apps."
            )
        raise C.WinError(code)


class Wifi:
    def __init__(self):
        self.dll = C.WinDLL("wlanapi.dll")
        signatures = {
            "WlanOpenHandle": [DWORD, PTR, C.POINTER(DWORD), C.POINTER(PTR)],
            "WlanCloseHandle": [PTR, PTR],
            "WlanEnumInterfaces": [PTR, PTR, C.POINTER(PTR)],
            "WlanQueryInterface": [
                PTR,
                C.POINTER(GUID),
                INT,
                PTR,
                C.POINTER(DWORD),
                C.POINTER(PTR),
                C.POINTER(INT),
            ],
            "WlanConnect": [PTR, C.POINTER(GUID), C.POINTER(Parameters), PTR],
        }
        for name, args in signatures.items():
            fn = getattr(self.dll, name)
            fn.argtypes, fn.restype = args, DWORD
        self.dll.WlanFreeMemory.argtypes = [PTR]
        self.dll.WlanFreeMemory.restype = None

    @contextmanager
    def session(self):
        # Reopen each cycle so WLAN service restarts do not leave a stale handle.
        handle, version = PTR(), DWORD()
        check(self.dll.WlanOpenHandle(2, None, C.byref(version), C.byref(handle)))
        try:
            yield handle
        finally:
            self.dll.WlanCloseHandle(handle, None)

    def interfaces(self, handle):
        ptr = PTR()
        check(self.dll.WlanEnumInterfaces(handle, None, C.byref(ptr)))
        try:
            count = DWORD.from_address(ptr.value).value
            start = ptr.value + 8  # DWORD count followed by DWORD index
            return [
                Interface.from_buffer_copy(
                    C.string_at(start + i * C.sizeof(Interface), C.sizeof(Interface))
                )
                for i in range(count)
            ]
        finally:
            self.dll.WlanFreeMemory(ptr)

    def connection(self, handle, guid):
        ptr, size, kind = PTR(), DWORD(), INT()
        code = self.dll.WlanQueryInterface(
            handle, C.byref(guid), 7, None, C.byref(size), C.byref(ptr), C.byref(kind)
        )
        if code == 5023:  # ERROR_INVALID_STATE: interface is disconnected
            return None
        check(code)
        try:
            if size.value < C.sizeof(ConnectionPrefix):
                raise OSError("Windows returned incomplete Wi-Fi connection data.")
            return ConnectionPrefix.from_buffer_copy(C.string_at(ptr, C.sizeof(ConnectionPrefix)))
        finally:
            self.dll.WlanFreeMemory(ptr)

    def connect(self, handle, guid, profile, ssid, bss_type):
        params = Parameters(0, profile, C.pointer(ssid), None, bss_type, 0)
        check(self.dll.WlanConnect(handle, C.byref(guid), C.byref(params), None))


def ssid_bytes(ssid):
    return bytes(ssid.data[: min(ssid.length, 32)])


def matches(current, profile, ssid):
    return (
        current is not None
        and current.state == 1
        and current.profile == profile
        and ssid_bytes(current.ssid) == ssid_bytes(ssid)
    )


def monitor(wifi, guid, profile, ssid, bss_type, sleep=time.sleep):
    was_connected = True
    while True:
        sleep(10)
        try:
            with wifi.session() as handle:
                current = wifi.connection(handle, guid)
                if matches(current, profile, ssid):
                    if not was_connected:
                        log(f"Reconnected to {profile!r}.")
                    was_connected = True
                else:
                    was_connected = False
                    log(f"Requesting reconnection to {profile!r}...")
                    wifi.connect(handle, guid, profile, ssid, bss_type)
                    # Request acceptance is not proof of a successful connection.
                    # The next check confirms whether connection actually succeeded.
        except OSError as exc:
            was_connected = False
            log(f"{exc} Retrying in 10 seconds.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Keep the current Windows Wi-Fi connected")
    parser.add_argument(
        "--background",
        action="store_true",
        help="Use the first connected adapter without prompting",
    )
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        print("This script requires Windows.", file=sys.stderr)
        return 1
    wifi = Wifi()
    with wifi.session() as handle:
        connected = [i for i in wifi.interfaces(handle) if i.state == 1]
        if not connected:
            raise OSError("Connect to the desired Wi-Fi network before starting this script.")
        if len(connected) > 1 and not args.background:
            for number, item in enumerate(connected, 1):
                print(f"{number}: {item.description}")
            try:
                choice = int(input("Select the adapter to keep connected: ")) - 1
                if not 0 <= choice < len(connected):
                    raise ValueError
            except ValueError:
                raise OSError("Invalid adapter selection.") from None
        else:
            choice = 0
        interface = connected[choice]
        current = wifi.connection(handle, interface.guid)
        if current is None or current.state != 1 or not current.profile:
            raise OSError("No connected saved Wi-Fi profile found. Connect and try again.")
        profile = current.profile
        ssid = SSID.from_buffer_copy(current.ssid)
        bss_type = current.bss_type
    log(f"Keeping {profile!r} connected on {interface.description}.")
    log("Checking every 10 seconds. Press Ctrl+C to stop.")
    monitor(wifi, interface.guid, profile, ssid, bss_type)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped.")
    except OSError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
