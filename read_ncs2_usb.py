#!/usr/bin/env python3
"""Read NCS2 USB descriptors with existing libusb; no firmware upload/reset."""
import ctypes as C
import ctypes.util
import argparse
import json
import os
import sys

class Descriptor(C.Structure):
    _fields_ = [
        ('bLength', C.c_uint8), ('bDescriptorType', C.c_uint8),
        ('bcdUSB', C.c_uint16), ('bDeviceClass', C.c_uint8),
        ('bDeviceSubClass', C.c_uint8), ('bDeviceProtocol', C.c_uint8),
        ('bMaxPacketSize0', C.c_uint8), ('idVendor', C.c_uint16),
        ('idProduct', C.c_uint16), ('bcdDevice', C.c_uint16),
        ('iManufacturer', C.c_uint8), ('iProduct', C.c_uint8),
        ('iSerialNumber', C.c_uint8), ('bNumConfigurations', C.c_uint8),
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--show-serial', action='store_true', help='Include device serial numbers in local output.')
    options = parser.parse_args()
    libpath = os.environ.get('NCS2_LIBUSB') or ctypes.util.find_library('usb-1.0') or '/opt/homebrew/opt/libusb/lib/libusb-1.0.dylib'
    lib = C.CDLL(libpath)
    signatures = {
        'libusb_init': ([C.POINTER(C.c_void_p)], C.c_int),
        'libusb_exit': ([C.c_void_p], None),
        'libusb_get_device_list': ([C.c_void_p, C.POINTER(C.POINTER(C.c_void_p))], C.c_ssize_t),
        'libusb_free_device_list': ([C.POINTER(C.c_void_p), C.c_int], None),
        'libusb_get_device_descriptor': ([C.c_void_p, C.POINTER(Descriptor)], C.c_int),
        'libusb_get_bus_number': ([C.c_void_p], C.c_uint8),
        'libusb_get_device_address': ([C.c_void_p], C.c_uint8),
        'libusb_get_device_speed': ([C.c_void_p], C.c_int),
        'libusb_open': ([C.c_void_p, C.POINTER(C.c_void_p)], C.c_int),
        'libusb_close': ([C.c_void_p], None),
        'libusb_get_string_descriptor_ascii': ([C.c_void_p, C.c_uint8, C.c_void_p, C.c_int], C.c_int),
        'libusb_control_transfer': ([C.c_void_p, C.c_uint8, C.c_uint8, C.c_uint16, C.c_uint16, C.c_void_p, C.c_uint16, C.c_uint], C.c_int),
        'libusb_error_name': ([C.c_int], C.c_char_p),
    }
    for name, (args, ret) in signatures.items():
        fn = getattr(lib, name)
        fn.argtypes, fn.restype = args, ret
    def error(code):
        return {'code': code, 'name': lib.libusb_error_name(code).decode()}
    ctx = C.c_void_p()
    rc = lib.libusb_init(C.byref(ctx))
    if rc < 0:
        print(json.dumps({'init_error': error(rc)}))
        return 1
    devices = C.POINTER(C.c_void_p)()
    count = lib.libusb_get_device_list(ctx, C.byref(devices))
    matches = []
    try:
        if count < 0:
            print(json.dumps({'enumeration_error': error(count)}))
            return 1
        for i in range(count):
            dev, desc = devices[i], Descriptor()
            if lib.libusb_get_device_descriptor(dev, C.byref(desc)) < 0 or desc.idVendor != 0x03e7:
                continue
            result = {
                'vid_pid': f'{desc.idVendor:04x}:{desc.idProduct:04x}',
                'bus': lib.libusb_get_bus_number(dev),
                'address': lib.libusb_get_device_address(dev),
                'libusb_speed': lib.libusb_get_device_speed(dev),
                'usb_version_bcd': f'{desc.bcdUSB:04x}',
                'configuration_count': desc.bNumConfigurations,
            }
            matches.append(result)
            handle = C.c_void_p()
            rc = lib.libusb_open(dev, C.byref(handle))
            result['open'] = error(rc)
            if rc < 0:
                continue
            try:
                for key, idx in [('manufacturer', desc.iManufacturer), ('product', desc.iProduct), ('serial', desc.iSerialNumber)]:
                    if key == 'serial' and not options.show_serial:
                        result[key] = '[redacted]'
                        continue
                    if not idx:
                        continue
                    buf = C.create_string_buffer(256)
                    n = lib.libusb_get_string_descriptor_ascii(handle, idx, buf, len(buf))
                    result[key] = buf.raw[:n].decode(errors='replace') if n >= 0 else error(n)
                buf = C.create_string_buffer(4096)
                n = lib.libusb_control_transfer(handle, 0x80, 6, 0x0200, 0, buf, len(buf), 2000)
                if n < 0:
                    result['configuration_descriptor'] = error(n)
                else:
                    raw = buf.raw[:n]
                    result['configuration_descriptor_hex'] = raw.hex()
                    endpoints = []
                    offset = 0
                    while offset + 2 <= n:
                        size, kind = raw[offset:offset + 2]
                        if size < 2 or offset + size > n:
                            break
                        chunk = raw[offset:offset + size]
                        if kind == 5 and size >= 7:
                            endpoints.append({'address': f'0x{chunk[2]:02x}', 'direction': 'IN' if chunk[2] & 0x80 else 'OUT', 'transfer_type': ['control', 'isochronous', 'bulk', 'interrupt'][chunk[3] & 3], 'max_packet_size': int.from_bytes(chunk[4:6], 'little')})
                        offset += size
                    result['endpoints'] = endpoints
            finally:
                lib.libusb_close(handle)
        print(json.dumps({'library': libpath, 'usb_device_count': count, 'movidius_devices': matches}, indent=2, ensure_ascii=False))
        return 0 if matches and all(x['open']['code'] == 0 for x in matches) else 2
    finally:
        if count >= 0:
            lib.libusb_free_device_list(devices, 1)
        lib.libusb_exit(ctx)

if __name__ == '__main__':
    sys.exit(main())
