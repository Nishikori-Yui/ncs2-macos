"""Read cached descriptors only: no device handle, interface claim, reset or transfer."""
import ctypes as C
import ctypes.util
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from read_ncs2_usb import Descriptor


class USBObserver:
    def __init__(self):
        self.path=os.environ.get('NCS2_LIBUSB') or ctypes.util.find_library('usb-1.0') or '/opt/homebrew/opt/libusb/lib/libusb-1.0.dylib'
        self.lib=C.CDLL(self.path)
        for name, arguments, restype in [
            ('libusb_init',[C.POINTER(C.c_void_p)],C.c_int),
            ('libusb_exit',[C.c_void_p],None),
            ('libusb_get_device_list',[C.c_void_p,C.POINTER(C.POINTER(C.c_void_p))],C.c_ssize_t),
            ('libusb_free_device_list',[C.POINTER(C.c_void_p),C.c_int],None),
            ('libusb_get_device_descriptor',[C.c_void_p,C.POINTER(Descriptor)],C.c_int),
            ('libusb_get_bus_number',[C.c_void_p],C.c_uint8),
            ('libusb_get_device_address',[C.c_void_p],C.c_uint8),
            ('libusb_get_device_speed',[C.c_void_p],C.c_int),
            ('libusb_get_port_numbers',[C.c_void_p,C.POINTER(C.c_uint8),C.c_int],C.c_int),
        ]:
            function=getattr(self.lib,name);function.argtypes=arguments;function.restype=restype

    def snapshot(self):
        context=C.c_void_p()
        code=self.lib.libusb_init(C.byref(context))
        if code<0:
            return {'error':'libusb_init','code':code}
        devices=C.POINTER(C.c_void_p)()
        count=-1
        try:
            count=self.lib.libusb_get_device_list(context,C.byref(devices))
            if count<0:
                return {'error':'libusb_get_device_list','code':count}
            matches=[]
            for index in range(count):
                device=devices[index];descriptor=Descriptor()
                if self.lib.libusb_get_device_descriptor(device,C.byref(descriptor))<0 or descriptor.idVendor!=0x03e7:
                    continue
                ports=(C.c_uint8*8)();length=self.lib.libusb_get_port_numbers(device,ports,len(ports))
                matches.append({'vid_pid':f'{descriptor.idVendor:04x}:{descriptor.idProduct:04x}',
                                'bus':self.lib.libusb_get_bus_number(device),
                                'address':self.lib.libusb_get_device_address(device),
                                'ports':list(ports[:length]) if length>=0 else [],
                                'libusb_speed':self.lib.libusb_get_device_speed(device),
                                'usb_version_bcd':f'{descriptor.bcdUSB:04x}'})
            return {'usb_device_count':count,'movidius_devices':sorted(matches,key=lambda item:(item['bus'],item['address']))}
        finally:
            if count>=0:
                self.lib.libusb_free_device_list(devices,1)
            self.lib.libusb_exit(context)
