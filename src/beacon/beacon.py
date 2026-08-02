from machine import Pin
from utime import sleep_us, sleep_ms

class Beacon:
    def __init__(self, id: str, led_gpio: Pin,
                 green_gpio: Pin, yellow_gpio: Pin, 
                 red_gpio: Pin):
        self._id: str = id
        self._led_gpio: Pin = led_gpio
        self._green_gpio: Pin = green_gpio
        self._yellow_gpio: Pin = yellow_gpio
        self._red_gpio: Pin = red_gpio
        
        # Define the pulse durations (in microseconds)
        self._pulse_0_us = 10_000   # 10ms ON for a '0'
        self._pulse_1_us = 25_000   # 25ms ON for a '1'
        self._pulse_gap_us = 10_000 # 10ms OFF between every bit
        
        # Ensure LED starts OFF (Idle state for IR)
        self._led_gpio.off() 
        self.set_stoplight(red=True, yellow=False, green=False)

    def set_stoplight(self, red: bool, yellow: bool, green: bool) -> None:
        """Helper to set the stoplight states."""
        self._red_gpio.value(red)
        self._yellow_gpio.value(yellow)
        self._green_gpio.value(green)

    def _set_emitting(self, is_emitting: bool) -> None:
        """Keeps the IR diode and the stoplight perfectly synced."""
        if is_emitting:
            self._led_gpio.on()
            self.set_stoplight(red=False, yellow=False, green=True)  # Emitting = Green
        else:
            self._led_gpio.off()
            self.set_stoplight(red=False, yellow=True, green=False)  # Idle = Yellow

    def send_bit(self, bit_val: int) -> None:
        """Sends a single bit using pulse width."""
        self._set_emitting(True)        # RISING edge
        
        if bit_val == 1:
            sleep_us(self._pulse_1_us)
        else:
            sleep_us(self._pulse_0_us)
            
        self._set_emitting(False)       # FALLING edge
        sleep_us(self._pulse_gap_us)    # Mandatory gap between bits

    def send_byte(self, c: str) -> None:
        """Sends an ASCII character bit by bit."""
        byte_val = ord(c)
        
        # Send a massive "Start Pulse"
        self._set_emitting(True)
        sleep_us(50_000) # 50ms start pulse
        self._set_emitting(False)
        sleep_us(self._pulse_gap_us)

        # Send the 8 data bits
        for i in range(8):
            bit = (byte_val >> i) & 1
            self.send_bit(bit)

    def broadcast_id(self) -> None:
        msg = self._id + "\n"
        print(f"Broadcasting: {msg.strip()}")
        
        # Switch from Red to Yellow (Idle) right before we start looping
        self.set_stoplight(red=False, yellow=True, green=False)
        
        while True:
            for c in msg:
                self.send_byte(c)
            sleep_ms(500) # Wait half a second before repeating the message

    def shutdown(self) -> None:
        """Safely turns off the IR LED and sets stoplight to RED."""
        print("Shutting down beacon...")
        self._led_gpio.off()
        self.set_stoplight(red=True, yellow=False, green=False)        


if __name__ == "__main__":
    # Setup hardware (Update pins to match your wiring)
    led_gpio = Pin(16, Pin.OUT)
    green_pin = Pin(1, Pin.OUT)
    yellow_pin = Pin(2, Pin.OUT)
    red_pin = Pin(0, Pin.OUT)
    
    test_beacon = Beacon(
        id="A", 
        led_gpio=led_gpio,
        green_gpio=green_pin,
        yellow_gpio=yellow_pin,
        red_gpio=red_pin
    )
    
    try:
        # Continuously send the ID
        test_beacon.broadcast_id()
    except KeyboardInterrupt:
        pass # Caught the stop command from the user
    finally:
        # Guarantee it goes back to RED when the program stops
        test_beacon.shutdown()
