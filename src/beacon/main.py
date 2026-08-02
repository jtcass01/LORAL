import network
import socket
import _thread
from machine import Pin
from utime import sleep_us, sleep_ms, sleep

# --- Shared State for Inter-Core Communication ---
msg_lock = _thread.allocate_lock()
shared_data = {
    "message": "A" # Default starting message
}

# --- Wi-Fi Setup ---
def connect_wifi(ssid, password):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(ssid, password)
    
    print("Connecting to WiFi...")
    while not wlan.isconnected():
        sleep(1)
        
    print("Connected! IP Address:", wlan.ifconfig()[0])

# --- Helper to decode URL characters from the web form ---
def url_decode(s):
    s = s.replace('+', ' ')
    res = ""
    i = 0
    while i < len(s):
        if s[i] == '%' and i + 2 < len(s):
            res += chr(int(s[i+1:i+3], 16))
            i += 3
        else:
            res += s[i]
            i += 1
    return res

# --- Core 1: HTTP Server ---
def core1_http_server():
    """Runs continuously on Core 1 to serve the web interface."""
    print("Starting HTTP Server on Core 1...")
    
    addr = socket.getaddrinfo('0.0.0.0', 80)[0][-1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(1)
    
    while True:
        try:
            cl, addr = s.accept()
            request = cl.recv(1024).decode('utf-8')
            
            # Very basic HTTP GET parsing
            try:
                request_line = request.split('\r\n')[0]
                method, path, version = request_line.split(' ')
                
                # Check if a form was submitted (e.g., /?msg=Hello+World)
                if '?' in path:
                    query = path.split('?')[1]
                    if query.startswith('msg='):
                        raw_msg = query.split('&')[0][4:] # Extract value after 'msg='
                        new_msg = url_decode(raw_msg)
                        
                        # Update the shared message safely
                        with msg_lock:
                            shared_data["message"] = new_msg
                            print(f"\n[HTTP Server] Message updated to: {new_msg}")
            except Exception as parse_err:
                pass # Ignore malformed requests

            # Safely read current message for the HTML
            with msg_lock:
                current_msg = shared_data["message"]
            
            # Build HTML response with a form
            html = f"""HTTP/1.1 200 OK\r\nContent-type: text/html\r\n\r\n
            <!DOCTYPE html>
            <html>
                <head>
                    <title>Beacon Controller</title>
                    <style>
                        body {{ font-family: sans-serif; margin: 2rem; background-color: #f4f4f9; }}
                        .container {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); max-width: 500px; }}
                        input[type=text] {{ padding: 8px; width: 70%; }}
                        input[type=submit] {{ padding: 8px 16px; background-color: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h2>IR Beacon Controller</h2>
                        <p>Currently Broadcasting: <b>{current_msg}</b></p>
                        
                        <form action="/" method="GET">
                            <input type="text" name="msg" placeholder="Enter new message..." required maxlength="32">
                            <input type="submit" value="Update">
                        </form>
                        <p style="font-size: 0.8em; color: gray;">Note: A newline character (\\n) is automatically appended during transmission.</p>
                    </div>
                </body>
            </html>
            """
            cl.send(html.encode('utf-8'))
            cl.close()
        except Exception as e:
            print("HTTP Server error:", e)
            if 'cl' in locals():
                cl.close()


# --- Core 0: Transmitter ---
class Beacon:
    def __init__(self, led_gpio: Pin, green_gpio: Pin, yellow_gpio: Pin, red_gpio: Pin):
        self._led_gpio: Pin = led_gpio
        self._green_gpio: Pin = green_gpio
        self._yellow_gpio: Pin = yellow_gpio
        self._red_gpio: Pin = red_gpio
        
        self._pulse_0_us = 10_000   
        self._pulse_1_us = 25_000   
        self._pulse_gap_us = 10_000 
        
        self._led_gpio.off() 
        self.set_stoplight(red=True, yellow=False, green=False)

    def set_stoplight(self, red: bool, yellow: bool, green: bool) -> None:
        self._red_gpio.value(red)
        self._yellow_gpio.value(yellow)
        self._green_gpio.value(green)

    def _set_emitting(self, is_emitting: bool) -> None:
        if is_emitting:
            self._led_gpio.on()
            self.set_stoplight(red=False, yellow=False, green=True)  
        else:
            self._led_gpio.off()
            self.set_stoplight(red=False, yellow=True, green=False)  

    def send_bit(self, bit_val: int) -> None:
        self._set_emitting(True)        
        if bit_val == 1:
            sleep_us(self._pulse_1_us)
        else:
            sleep_us(self._pulse_0_us)
            
        self._set_emitting(False)       
        sleep_us(self._pulse_gap_us)    

    def send_byte(self, c: str) -> None:
        byte_val = ord(c)
        self._set_emitting(True)
        sleep_us(50_000) 
        self._set_emitting(False)
        sleep_us(self._pulse_gap_us)

        for i in range(8):
            bit = (byte_val >> i) & 1
            self.send_bit(bit)

    def broadcast_loop(self) -> None:
        print("Starting broadcast loop...")
        self.set_stoplight(red=False, yellow=True, green=False)
        
        while True:
            # Safely fetch the latest message on every pass and automatically append \n
            with msg_lock:
                current_msg = shared_data["message"] + "\n"
            
            for c in current_msg:
                self.send_byte(c)
            
            # Wait half a second before repeating the message
            sleep_ms(500) 

    def shutdown(self) -> None:
        print("Shutting down beacon...")
        self._led_gpio.off()
        self.set_stoplight(red=True, yellow=False, green=False)        


if __name__ == "__main__":
    # 1. Connect to Network
    connect_wifi("", "")
    
    # Setup hardware
    led_gpio: Pin = Pin(16, Pin.OUT)
    green_pin: Pin = Pin(1, Pin.OUT)
    yellow_pin: Pin = Pin(2, Pin.OUT)
    red_pin: Pin = Pin(0, Pin.OUT)
    
    test_beacon = Beacon(
        led_gpio=led_gpio,
        green_gpio=green_pin,
        yellow_gpio=yellow_pin,
        red_gpio=red_pin
    )
    
    # 2. Start HTTP server on Core 1
    _thread.start_new_thread(core1_http_server, ())
    
    try:
        # 3. Run broadcast loop on Core 0
        test_beacon.broadcast_loop()
    except KeyboardInterrupt:
        pass 
    finally:
        test_beacon.shutdown()