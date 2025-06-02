import serial
import struct
import time
import glob
from typing import Tuple, Optional, Dict, List
from enum import IntEnum

class VoltageRange(IntEnum):
    """Voltage range constants"""
    V15 = 0
    V30 = 1
    V60 = 2
    V100 = 3
    V120 = 4
    V150 = 5
    V160 = 6
    V200 = 7
    V300 = 8

class CurrentRange(IntEnum):
    """Current range constants"""
    A1 = 0
    A2 = 1
    A3 = 2
    A5 = 3
    A6 = 4
    A10 = 5
    A20 = 6
    A30 = 7
    A40 = 8
    A50 = 9
    A60 = 10
    A80 = 11
    A100 = 12
    A200 = 13

class WanptekPowerSupply:
    """
    Universal Python controller for WANPTEK DC Power Supply using MODBUS-RTU protocol.
    
    Supports all WANPTEK models with automatic device detection and configuration.
    Tested on Linux with /dev/ttyUSB* devices.
    
    Features:
    - Auto-detect device specifications and capabilities
    - Set/read voltage and current with proper scaling
    - Power control (on/off)
    - OCP (Over Current Protection) control
    - Keyboard lock control
    - Real-time status monitoring
    - Support for all voltage/current ranges
    - Automatic endianness detection
    - Robust error handling and reconnection
    """
    
    # Voltage range mapping (series_code -> max_voltage)
    VOLTAGE_RANGES = {
        0: 15, 1: 30, 2: 60, 3: 100, 4: 120, 
        5: 150, 6: 160, 7: 200, 8: 300
    }
    
    # Current range mapping (series_code -> max_current)  
    CURRENT_RANGES = {
        0: 1, 1: 2, 2: 3, 3: 5, 4: 6, 5: 10, 6: 20,
        7: 30, 8: 40, 9: 50, 10: 60, 11: 80, 12: 100, 13: 200
    }
    
    # Standard baudrates supported by WANPTEK devices
    SUPPORTED_BAUDRATES = [2400, 4800, 9600, 19200]
    
    def __init__(self, port: Optional[str] = None, slave_addr: int = 0, 
                 baudrate: Optional[int] = None, timeout: float = 1.0, 
                 auto_detect: bool = True):
        """
        Initialize the power supply controller with universal compatibility.
        
        Args:
            port: Serial port (e.g., '/dev/ttyUSB0'). If None, auto-detect.
            slave_addr: Device address (0-31)
            baudrate: Communication speed. If None, auto-detect.
            timeout: Serial communication timeout in seconds
            auto_detect: Try to auto-detect port and baudrate
        """
        self.slave_addr = slave_addr
        self.timeout = timeout
        self.serial = None
        
        # Device specifications (detected from device)
        self.voltage_decimal_places = 2
        self.current_decimal_places = 2
        self.voltage_series = 0
        self.current_series = 0
        self.max_voltage = 0
        self.max_current = 0
        self.nominal_voltage = 0
        self.nominal_current = 0
        self.little_endian = True
        self.device_model = "Unknown"
        
        # Connection status
        self.connected = False
        self.last_status = {}
        
        if auto_detect:
            self._auto_connect(port, baudrate)
        else:
            if port is None:
                raise ValueError("Port must be specified when auto_detect=False")
            if baudrate is None:
                baudrate = 9600
            self._connect(port, baudrate)
    
    @staticmethod
    def find_devices() -> List[str]:
        """Find all potential WANPTEK devices on Linux"""
        devices = []
        # Check common USB serial device paths
        for pattern in ['/dev/ttyUSB*', '/dev/ttyACM*', '/dev/serial/by-id/*']:
            devices.extend(glob.glob(pattern))
        return sorted(devices)
    
    def _auto_connect(self, preferred_port: Optional[str] = None, 
                     preferred_baudrate: Optional[int] = None):
        """Auto-detect and connect to WANPTEK device"""
        print("🔍 Auto-detecting WANPTEK power supply...")
        
        # Get list of potential devices
        if preferred_port:
            ports_to_try = [preferred_port]
        else:
            ports_to_try = self.find_devices()
            if not ports_to_try:
                ports_to_try = ['/dev/ttyUSB0']  # Fallback
        
        # Get list of baudrates to try
        if preferred_baudrate:
            baudrates_to_try = [preferred_baudrate]
        else:
            baudrates_to_try = [9600, 4800, 19200, 2400]  # Most common first
        
        # Try each combination
        for port in ports_to_try:
            print(f"  📡 Trying port: {port}")
            for baudrate in baudrates_to_try:
                try:
                    if self._connect(port, baudrate, silent=True):
                        print(f"  ✅ Connected to {self.device_model} at {port} ({baudrate} baud)")
                        return
                except Exception:
                    continue
        
        raise Exception("❌ Could not auto-detect WANPTEK device. Please specify port and baudrate manually.")
    
    def _connect(self, port: str, baudrate: int, silent: bool = False) -> bool:
        """Connect to device and verify communication"""
        try:
            if self.serial and self.serial.is_open:
                self.serial.close()
            
            self.serial = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=8,
                parity=serial.PARITY_NONE,
                stopbits=1,
                timeout=self.timeout
            )
            
            # Test communication by reading status
            self._detect_device_specs()
            self.connected = True
            
            if not silent:
                print(f"✅ Connected to {self.device_model}")
                self._print_device_info()
            
            return True
            
        except Exception as e:
            if not silent:
                print(f"❌ Connection failed: {e}")
            if self.serial and self.serial.is_open:
                self.serial.close()
            return False
    
    def _detect_device_specs(self):
        """Detect device specifications from status response"""
        status = self._read_raw_status()
        
        # Extract device specifications
        data = status[3:19]
        
        # Parse configuration bytes
        voltage_info = data[1]
        current_info = data[2]
        
        self.voltage_decimal_places = 1 if (voltage_info >> 4) & 0x0F else 2
        self.current_decimal_places = 1 if (current_info >> 4) & 0x0F else 2
        
        self.voltage_series = voltage_info & 0x0F
        self.current_series = current_info & 0x0F
        
        # Determine endianness
        status_byte = data[0]
        self.little_endian = not bool(status_byte & 0x08)
        
        # Get nominal and max values
        self.nominal_voltage = self.VOLTAGE_RANGES.get(self.voltage_series, 0)
        self.nominal_current = self.CURRENT_RANGES.get(self.current_series, 0)
        
        # Parse actual max values from device
        max_voltage_raw = self._unpack_word(data[12:14])
        max_current_raw = self._unpack_word(data[14:16])
        
        voltage_divisor = 10 ** self.voltage_decimal_places
        current_divisor = 10 ** self.current_decimal_places
        
        self.max_voltage = max_voltage_raw / voltage_divisor
        self.max_current = max_current_raw / current_divisor
        
        # Determine device model
        self.device_model = f"WANPTEK {self.nominal_voltage}V/{self.nominal_current}A"
    
    def _print_device_info(self):
        """Print detected device information"""
        print(f"📋 Device Information:")
        print(f"   Model: {self.device_model}")
        print(f"   Nominal: {self.nominal_voltage}V / {self.nominal_current}A")
        print(f"   Max Output: {self.max_voltage}V / {self.max_current}A")
        print(f"   Precision: {self.voltage_decimal_places} decimal places (V), {self.current_decimal_places} decimal places (A)")
        print(f"   Endianness: {'Little' if self.little_endian else 'Big'}")
        print(f"   Address: {self.slave_addr}")
    
    def _calculate_crc(self, data: bytes) -> int:
        """Calculate CRC16 with polynomial 0x8005 (MODBUS standard)"""
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc
    
    def _pack_word(self, value: int) -> bytes:
        """Pack 16-bit word according to detected endianness"""
        if self.little_endian:
            return struct.pack('<H', value)
        else:
            return struct.pack('>H', value)
    
    def _unpack_word(self, data: bytes) -> int:
        """Unpack 16-bit word according to detected endianness"""
        if self.little_endian:
            return struct.unpack('<H', data)[0]
        else:
            return struct.unpack('>H', data)[0]
    
    def _send_command(self, command: bytes) -> bytes:
        """Send command and receive response with error handling"""
        if not self.connected or not self.serial or not self.serial.is_open:
            raise Exception("Device not connected")
        
        # Add CRC
        crc = self._calculate_crc(command)
        full_command = command + struct.pack('<H', crc)
        
        # Clear input buffer
        self.serial.reset_input_buffer()
        
        # Send command
        self.serial.write(full_command)
        self.serial.flush()
        
        # Read response with timeout handling
        response = b''
        start_time = time.time()
        
        while len(response) < 3 and (time.time() - start_time) < self.timeout:
            chunk = self.serial.read(1024)
            response += chunk
            if len(chunk) == 0:
                time.sleep(0.01)  # Small delay to prevent busy waiting
        
        if len(response) < 3:
            raise Exception(f"Timeout: received only {len(response)} bytes")
        
        # Verify CRC
        data_part = response[:-2]
        received_crc = struct.unpack('<H', response[-2:])[0]
        calculated_crc = self._calculate_crc(data_part)
        
        if received_crc != calculated_crc:
            raise Exception(f"CRC verification failed: expected {calculated_crc:04X}, got {received_crc:04X}")
        
        return response
    
    def _read_raw_status(self) -> bytes:
        """Read raw status response from device"""
        command = struct.pack('BBHH', self.slave_addr, 0x03, 0x0000, 0x0008)
        response = self._send_command(command)
        
        if len(response) < 21:
            raise Exception(f"Invalid response length: expected 21, got {len(response)}")
        
        return response
    
    def read_status(self) -> Dict:
        """
        Read complete status information from the power supply.
        
        Returns:
            dict: Complete status with all measurements and flags
        """
        response = self._read_raw_status()
        data = response[3:19]
        
        # Parse status flags (byte 0)
        status_byte = data[0]
        power_on = bool(status_byte & 0x01)
        ocp_enabled = bool(status_byte & 0x02)
        keyboard_locked = bool(status_byte & 0x04)
        is_big_endian = bool(status_byte & 0x08)
        constant_current = bool(status_byte & 0x10)
        alarm_active = bool(status_byte & 0x20)
        
        # Parse measurement values
        real_voltage_raw = self._unpack_word(data[4:6])
        real_current_raw = self._unpack_word(data[6:8])
        set_voltage_raw = self._unpack_word(data[8:10])
        set_current_raw = self._unpack_word(data[10:12])
        
        # Convert to actual values
        voltage_divisor = 10 ** self.voltage_decimal_places
        current_divisor = 10 ** self.current_decimal_places
        
        status_dict = {
            # Control flags
            'power_on': power_on,
            'ocp_enabled': ocp_enabled,
            'keyboard_locked': keyboard_locked,
            'constant_current_mode': constant_current,
            'alarm_active': alarm_active,
            
            # Measurements
            'real_voltage': real_voltage_raw / voltage_divisor,
            'real_current': real_current_raw / current_divisor,
            'set_voltage': set_voltage_raw / voltage_divisor,
            'set_current': set_current_raw / current_divisor,
            
            # Device specs
            'max_voltage': self.max_voltage,
            'max_current': self.max_current,
            'nominal_voltage': self.nominal_voltage,
            'nominal_current': self.nominal_current,
            'voltage_series': self.voltage_series,
            'current_series': self.current_series,
            'model': self.device_model,
            
            # Power calculation
            'real_power': (real_voltage_raw / voltage_divisor) * (real_current_raw / current_divisor),
            'set_power': (set_voltage_raw / voltage_divisor) * (set_current_raw / current_divisor)
        }
        
        self.last_status = status_dict
        return status_dict
    
    def set_output(self, voltage: Optional[float] = None, current: Optional[float] = None, 
                   power_on: Optional[bool] = None, ocp_enable: Optional[bool] = None, 
                   keyboard_lock: Optional[bool] = None) -> bool:
        """
        Universal output control method.
        
        Args:
            voltage: Target voltage in volts (None = keep current setting)
            current: Target current in amps (None = keep current setting)
            power_on: Enable power output (None = keep current setting)
            ocp_enable: Enable over-current protection (None = keep current setting)
            keyboard_lock: Lock device keyboard (None = keep current setting)
            
        Returns:
            bool: True if successful
        """
        # Get current settings for any unspecified parameters
        current_status = self.read_status()
        
        # Use provided values or fall back to current settings
        target_voltage = voltage if voltage is not None else current_status['set_voltage']
        target_current = current if current is not None else current_status['set_current']
        target_power = power_on if power_on is not None else current_status['power_on']
        target_ocp = ocp_enable if ocp_enable is not None else current_status['ocp_enabled']
        target_lock = keyboard_lock if keyboard_lock is not None else current_status['keyboard_locked']
        
        # Validate ranges
        if target_voltage > self.max_voltage:
            raise ValueError(f"Voltage {target_voltage}V exceeds maximum {self.max_voltage}V")
        if target_current > self.max_current:
            raise ValueError(f"Current {target_current}A exceeds maximum {self.max_current}A")
        if target_voltage < 0 or target_current < 0:
            raise ValueError("Voltage and current must be non-negative")
        
        # Convert to raw values
        voltage_divisor = 10 ** self.voltage_decimal_places
        current_divisor = 10 ** self.current_decimal_places
        
        voltage_raw = int(target_voltage * voltage_divisor)
        current_raw = int(target_current * current_divisor)
        
        # Build control byte
        control_byte = 0
        if target_power:
            control_byte |= 0x01
        if target_ocp:
            control_byte |= 0x02
        if target_lock:
            control_byte |= 0x04
        
        # Build write command
        command = struct.pack('BBHHB', self.slave_addr, 0x10, 0x0000, 0x0003, 0x06)
        
        # Add data: control_byte + reserve + voltage + current
        data = struct.pack('BB', control_byte, 0x00)  # Control + Reserve
        data += self._pack_word(voltage_raw)  # Set voltage
        data += self._pack_word(current_raw)  # Set current
        
        full_command = command + data
        
        try:
            response = self._send_command(full_command)
            return len(response) >= 8
        except Exception as e:
            print(f"❌ Set output failed: {e}")
            return False
    
    # Convenience methods for common operations
    def set_voltage(self, voltage: float) -> bool:
        """Set output voltage while keeping other settings unchanged"""
        return self.set_output(voltage=voltage)
    
    def set_current(self, current: float) -> bool:
        """Set output current while keeping other settings unchanged"""
        return self.set_output(current=current)
    
    def power_on(self) -> bool:
        """Turn on the power output"""
        return self.set_output(power_on=True)
    
    def power_off(self) -> bool:
        """Turn off the power output"""
        return self.set_output(power_on=False)
    
    def enable_ocp(self) -> bool:
        """Enable over-current protection"""
        return self.set_output(ocp_enable=True)
    
    def disable_ocp(self) -> bool:
        """Disable over-current protection"""
        return self.set_output(ocp_enable=False)
    
    def lock_keyboard(self) -> bool:
        """Lock device keyboard (PC control only)"""
        return self.set_output(keyboard_lock=True)
    
    def unlock_keyboard(self) -> bool:
        """Unlock device keyboard"""
        return self.set_output(keyboard_lock=False)
    
    # Quick read methods
    def read_voltage(self) -> float:
        """Read actual output voltage"""
        return self.read_status()['real_voltage']
    
    def read_current(self) -> float:  
        """Read actual output current"""
        return self.read_status()['real_current']
    
    def read_power(self) -> float:
        """Read actual output power (V × A)"""
        status = self.read_status()
        return status['real_voltage'] * status['real_current']
    
    def is_power_on(self) -> bool:
        """Check if power output is enabled"""
        return self.read_status()['power_on']
    
    def is_constant_current(self) -> bool:
        """Check if device is in constant current mode"""
        return self.read_status()['constant_current_mode']
    
    def has_alarm(self) -> bool:
        """Check if device has active alarms"""
        return self.read_status()['alarm_active']
    
    # Utility methods
    def get_device_info(self) -> Dict:
        """Get comprehensive device information"""
        return {
            'model': self.device_model,
            'nominal_voltage': self.nominal_voltage,
            'nominal_current': self.nominal_current,
            'max_voltage': self.max_voltage,
            'max_current': self.max_current,
            'voltage_precision': self.voltage_decimal_places,
            'current_precision': self.current_decimal_places,
            'voltage_series': self.voltage_series,
            'current_series': self.current_series,
            'endianness': 'Little' if self.little_endian else 'Big',
            'slave_address': self.slave_addr,
            'connected': self.connected,
            'port': self.serial.port if self.serial else None,
            'baudrate': self.serial.baudrate if self.serial else None
        }
    
    def print_status(self):
        """Print formatted status information"""
        status = self.read_status()
        print(f"\n📊 {self.device_model} Status:")
        print(f"   Power: {'🟢 ON' if status['power_on'] else '🔴 OFF'}")
        print(f"   Output: {status['real_voltage']:.{self.voltage_decimal_places}f}V / {status['real_current']:.{self.current_decimal_places}f}A ({status['real_power']:.2f}W)")
        print(f"   Settings: {status['set_voltage']:.{self.voltage_decimal_places}f}V / {status['set_current']:.{self.current_decimal_places}f}A")
        print(f"   Mode: {'CC (Constant Current)' if status['constant_current_mode'] else 'CV (Constant Voltage)'}")
        print(f"   OCP: {'🟢 Enabled' if status['ocp_enabled'] else '🔴 Disabled'}")
        print(f"   Keyboard: {'🔒 Locked' if status['keyboard_locked'] else '🔓 Unlocked'}")
        if status['alarm_active']:
            print(f"   ⚠️  ALARM ACTIVE")
    
    def reconnect(self) -> bool:
        """Attempt to reconnect to the device"""
        if self.serial and hasattr(self.serial, 'port') and hasattr(self.serial, 'baudrate'):
            return self._connect(self.serial.port, self.serial.baudrate)
        else:
            return False
    
    def close(self):
        """Close the serial connection"""
        if self.serial and self.serial.is_open:
            self.serial.close()
        self.connected = False
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# Advanced usage examples and utilities
class WanptekMonitor:
    """Continuous monitoring utility for WANPTEK power supplies"""
    
    def __init__(self, psu: WanptekPowerSupply, interval: float = 1.0):
        self.psu = psu
        self.interval = interval
        self.monitoring = False
        
    def start_monitoring(self, callback=None):
        """Start continuous monitoring"""
        self.monitoring = True
        print(f"🔄 Starting monitoring every {self.interval}s (Ctrl+C to stop)")
        
        try:
            while self.monitoring:
                status = self.psu.read_status()
                
                if callback:
                    callback(status)
                else:
                    # Default display
                    print(f"\r{status['real_voltage']:.2f}V {status['real_current']:.3f}A {status['real_power']:.2f}W {'ON' if status['power_on'] else 'OFF'}", end='', flush=True)
                
                time.sleep(self.interval)
                
        except KeyboardInterrupt:
            self.monitoring = False
            print("\n⏹️  Monitoring stopped")


# Example usage and testing
if __name__ == "__main__":
    print("🔌 WANPTEK Universal Power Supply Controller")
    print("=" * 50)
    
    try:
        # Auto-detect and connect (preferred method for Linux)
        with WanptekPowerSupply(port='/dev/ttyUSB0', auto_detect=True) as psu:
            
            # Display device information  
            psu.print_status()
            
            # Test basic operations
            print(f"\n🧪 Testing basic operations...")
            
            # Set 5V, 1A
            print("Setting 5.0V, 1.0A...")
            psu.set_output(voltage=5.0, current=1.0, power_on=False)
            
            # Turn on power
            print("Turning on power...")
            psu.power_on()
            time.sleep(0.5)
            
            # Read actual values
            voltage = psu.read_voltage()
            current = psu.read_current()
            power = psu.read_power()
            print(f"✅ Output: {voltage:.2f}V, {current:.3f}A, {power:.2f}W")
            
            # Test current limit
            print("Testing current limit at 0.5A...")
            psu.set_current(0.5)
            time.sleep(0.5)
            
            if psu.is_constant_current():
                print("✅ Device entered constant current mode")
            
            # Turn off
            print("Turning off power...")
            psu.power_off()
            
            print("✅ All tests completed successfully!")
            
    except Exception as e:
        print(f"❌ Error: {e}")
        print("\n💡 Troubleshooting tips:")
        print("   - Check device is connected to /dev/ttyUSB0")
        print("   - Verify device address (default: 0)")
        print("   - Try different baudrates: 9600, 4800, 19200, 2400")
        print("   - Check USB cable and connections")
