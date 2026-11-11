#!/usr/bin/env python3
"""Read NCS2 USB descriptors with existing libusb; no firmware upload/reset."""
import ctypes as C
import ctypes.util
import argparse
import json
import os
from pathlib import Path
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


TRANSFER_TYPES = ('control', 'isochronous', 'bulk', 'interrupt')
DESCRIPTOR_TYPES = {
    1: 'device', 2: 'configuration', 3: 'string', 4: 'interface', 5: 'endpoint',
    6: 'device_qualifier', 7: 'other_speed_configuration', 0x0b: 'interface_association',
    0x21: 'hid', 0x22: 'report', 0x23: 'physical', 0x24: 'cs_interface',
    0x25: 'cs_endpoint',
}


def parse_configuration_descriptor(raw):
    """Decode one complete USB configuration descriptor without touching USB."""
    if not raw:
        raise ValueError('configuration descriptor is empty')
    descriptors = []
    offset = 0
    while offset < len(raw):
        if len(raw) - offset < 2:
            raise ValueError(f'truncated descriptor header at offset {offset}')
        length, kind = raw[offset], raw[offset + 1]
        if length < 2:
            raise ValueError(f'invalid descriptor length {length} at offset {offset}')
        end = offset + length
        if end > len(raw):
            raise ValueError(
                f'truncated descriptor at offset {offset}: need {length} bytes, '
                f'have {len(raw) - offset}')
        chunk = raw[offset:end]
        item = {
            'offset': offset,
            'length': length,
            'type': kind,
            'type_name': DESCRIPTOR_TYPES.get(kind, f'unknown_0x{kind:02x}'),
            'raw_hex': chunk.hex(),
        }
        if kind == 2:
            if offset != 0 or length < 9:
                raise ValueError(f'invalid configuration descriptor at offset {offset}')
            item.update({
                'total_length': int.from_bytes(chunk[2:4], 'little'),
                'interface_count': chunk[4],
                'configuration_value': chunk[5],
                'attributes': chunk[7],
                'max_power_ma': chunk[8] * 2,
            })
        elif kind == 4:
            if length < 9:
                raise ValueError(f'invalid interface descriptor at offset {offset}')
            item.update({
                'interface_number': chunk[2],
                'alternate_setting': chunk[3],
                'endpoint_count': chunk[4],
                'class': chunk[5],
                'subclass': chunk[6],
                'protocol': chunk[7],
            })
        elif kind == 5:
            if length < 7:
                raise ValueError(f'invalid endpoint descriptor at offset {offset}')
            item.update({
                'address': f'0x{chunk[2]:02x}',
                'direction': 'IN' if chunk[2] & 0x80 else 'OUT',
                'transfer_type': TRANSFER_TYPES[chunk[3] & 3],
                'max_packet_size': int.from_bytes(chunk[4:6], 'little') & 0x07ff,
                'interval': chunk[6],
            })
        descriptors.append(item)
        offset = end

    total_length = descriptors[0].get('total_length')
    if total_length != len(raw):
        raise ValueError(
            f'configuration wTotalLength={total_length} does not match bytes={len(raw)}')
    interfaces = []
    current = None
    endpoints = []
    for item in descriptors:
        if item['type'] == 4:
            current = {**item, 'endpoints': []}
            interfaces.append(current)
        elif item['type'] == 5:
            endpoints.append(item)
            if current is None:
                raise ValueError(f'endpoint descriptor at offset {item["offset"]} has no interface')
            current['endpoints'].append(item)
    for interface in interfaces:
        actual = len(interface['endpoints'])
        interface['actual_endpoint_count'] = actual
        if actual != interface['endpoint_count']:
            raise ValueError(
                f'interface {interface["interface_number"]} declares '
                f'{interface["endpoint_count"]} endpoints, found {actual}')
    configuration = descriptors[0]
    interface_numbers = {item['interface_number'] for item in interfaces}
    if len(interface_numbers) != configuration['interface_count']:
        raise ValueError(
            f'configuration declares {configuration["interface_count"]} interfaces, '
            f'found {len(interface_numbers)} interface numbers')
    return {
        'total_length': total_length,
        'descriptor_count': len(descriptors),
        'interface_number_count': len(interface_numbers),
        'configuration': configuration,
        'interfaces': interfaces,
        'endpoints': endpoints,
        'descriptors': descriptors,
    }


def read_hex_input(value):
    """Read hex directly or from @PATH for offline descriptor archaeology."""
    source = value
    if value.startswith('@'):
        source = Path(value[1:]).read_text(encoding='utf-8')
    try:
        return bytes.fromhex(source)
    except ValueError as error:
        raise ValueError('descriptor input is not valid hexadecimal') from error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--show-serial', action='store_true', help='Include device serial numbers in local output.')
    parser.add_argument('--parse-configuration-hex', metavar='HEX_OR_@PATH',
                        help='Parse a saved configuration descriptor without opening USB.')
    options = parser.parse_args(argv)
    if options.parse_configuration_hex:
        try:
            parsed = parse_configuration_descriptor(read_hex_input(options.parse_configuration_hex))
        except (OSError, ValueError) as error:
            print(json.dumps({'offline_parse_error': str(error)}, indent=2))
            return 2
        print(json.dumps({'source': 'offline', 'configuration_descriptor': parsed}, indent=2))
        return 0
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
    parse_errors = []
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
                    try:
                        parsed = parse_configuration_descriptor(raw)
                    except ValueError as error:
                        result['configuration_descriptor_parse_error'] = str(error)
                        parse_errors.append(str(error))
                    else:
                        result['configuration_descriptor'] = parsed
                        # Keep the original flat field for existing consumers.
                        result['endpoints'] = parsed['endpoints']
            finally:
                lib.libusb_close(handle)
        print(json.dumps({'library': libpath, 'usb_device_count': count, 'movidius_devices': matches}, indent=2, ensure_ascii=False))
        return 0 if matches and not parse_errors and all(x['open']['code'] == 0 for x in matches) else 2
    finally:
        if count >= 0:
            lib.libusb_free_device_list(devices, 1)
        lib.libusb_exit(ctx)

if __name__ == '__main__':
    sys.exit(main())
