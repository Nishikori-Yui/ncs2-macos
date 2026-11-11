"""Offline tests for the USB descriptor archaeology parser."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('read_ncs2_usb', ROOT / 'read_ncs2_usb.py')
usb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usb)

CONFIGURATION = bytes.fromhex(
    '0902200001010080fa'
    '0904000002ff11ff00'
    '07058102000200'
    '07050102000200'
)


class USBParserTests(unittest.TestCase):
    def test_ncs2_configuration_is_decoded_hierarchically(self):
        parsed = usb.parse_configuration_descriptor(CONFIGURATION)
        self.assertEqual(parsed['total_length'], len(CONFIGURATION))
        self.assertEqual(parsed['descriptor_count'], 4)
        self.assertEqual(parsed['configuration']['interface_count'], 1)
        self.assertEqual(len(parsed['interfaces']), 1)
        interface = parsed['interfaces'][0]
        self.assertEqual(interface['actual_endpoint_count'], 2)
        self.assertEqual([endpoint['address'] for endpoint in parsed['endpoints']], ['0x81', '0x01'])
        self.assertEqual([endpoint['transfer_type'] for endpoint in interface['endpoints']], ['bulk', 'bulk'])
        self.assertEqual([endpoint['max_packet_size'] for endpoint in parsed['endpoints']], [512, 512])

    def test_alternate_settings_count_as_one_configuration_interface(self):
        raw = bytes.fromhex(
            '09021b0001010080fa'
            '0904000000ff11ff00'
            '0904000100ff11ff00'
        )
        parsed = usb.parse_configuration_descriptor(raw)
        self.assertEqual(parsed['interface_number_count'], 1)
        self.assertEqual([item['alternate_setting'] for item in parsed['interfaces']], [0, 1])

    def test_truncated_descriptor_fails_with_offset(self):
        with self.assertRaisesRegex(ValueError, 'truncated descriptor'):
            usb.parse_configuration_descriptor(CONFIGURATION[:-1])

    def test_declared_total_length_mismatch_fails(self):
        raw = bytearray(CONFIGURATION)
        raw[2:4] = (len(raw) + 1).to_bytes(2, 'little')
        with self.assertRaisesRegex(ValueError, 'wTotalLength'):
            usb.parse_configuration_descriptor(bytes(raw))

    def test_endpoint_without_interface_fails(self):
        raw = bytes.fromhex('0902100001010080fa07058102000200')
        with self.assertRaisesRegex(ValueError, 'has no interface'):
            usb.parse_configuration_descriptor(raw)

    def test_offline_cli_never_requires_libusb(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / 'read_ncs2_usb.py'), '--parse-configuration-hex', CONFIGURATION.hex()],
            capture_output=True, text=True, check=False,
            env={**os.environ, 'NCS2_LIBUSB': '/does/not/exist'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"source": "offline"', result.stdout)
        self.assertIn('"descriptor_count": 4', result.stdout)

    def test_offline_cli_rejects_malformed_hex(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / 'read_ncs2_usb.py'), '--parse-configuration-hex', 'not-hex'],
            capture_output=True, text=True, check=False,
            env={**os.environ, 'NCS2_LIBUSB': '/does/not/exist'})
        self.assertEqual(result.returncode, 2)
        self.assertIn('not valid hexadecimal', result.stdout)

    def test_offline_cli_reads_hex_from_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'configuration.hex'
            source.write_text(CONFIGURATION.hex() + '\n', encoding='ascii')
            result = subprocess.run(
                [sys.executable, str(ROOT / 'read_ncs2_usb.py'), '--parse-configuration-hex', '@' + str(source)],
                capture_output=True, text=True, check=False,
                env={**os.environ, 'NCS2_LIBUSB': '/does/not/exist'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"source": "offline"', result.stdout)


if __name__ == '__main__':
    unittest.main()
