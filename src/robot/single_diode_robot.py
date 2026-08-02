import uasyncio as asyncio
import network
import socket
import _thread
from machine import ADC
from utime import ticks_us, ticks_diff, sleep

# --- Shared State for Inter-Core Communication ---
data_lock = _thread.allocate_lock()
MAX_MESSAGES = 5 # Changed to keep the last 5 messages

shared_data = {
    "messages": ["System initialized. Awaiting messages..."],
    "estimated_distance": "Pending ML Model (TBD)"
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

# --- Core 1: HTTP Server ---
def core1_http_server():
    """Runs continuously on Core 1 to serve HTTP requests."""
    print("Starting HTTP Server on Core 1...")
    
    addr = socket.getaddrinfo('0.0.0.0', 80)[0][-1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(1)
    
    while True:
        try:
            cl, addr = s.accept()
            request = cl.recv(1024)
            
            # Safely read the shared data
            with data_lock:
                message_log = "\n".join(shared_data["messages"])
                dist = shared_data["estimated_distance"]
            
            # Build the HTML response
            html = f"""HTTP/1.1 200 OK\r\nContent-type: text/html\r\n\r\n
            <!DOCTYPE html>
            <html>
                <head>
                    <title>Robot Receiver Status</title>
                    <meta http-equiv="refresh" content="2">
                    <style>
                        body {{ font-family: sans-serif; margin: 2rem; background-color: #f4f4f9; }}
                        .container {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
                        .log-box {{ background: #1e1e1e; color: #00ff00; padding: 15px; border-radius: 5px; overflow-y: auto; height: 300px; }}
                        h3 {{ margin-top: 0; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h2>Robot Receiver Status</h2>
                        <p><b>Estimated Distance:</b> <span style="font-size: 1.2em; color: #d9534f;">{dist}</span></p>
                        
                        <h3>Message Log (Last {MAX_MESSAGES})</h3>
                        <div class="log-box">
<pre>{message_log}</pre>
                        </div>
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

# --- Core 0: Receiver ---
class SingleDiodeRobot:
    def __init__(self, receiver_adc: ADC, edge_threshold: int = 2000):
        self._adc: ADC = receiver_adc
        self._edge_threshold: int = edge_threshold
        self._prev_raw: int = 0
        self._filtered_val: float = 0.0
        self._alpha: float = 0.85

    def sample_hpf(self) -> tuple[int, float]:
        raw = self._adc.read_u16()
        self._filtered_val = (raw - self._prev_raw) + (self._alpha * self._filtered_val)
        self._prev_raw = raw
        return raw, self._filtered_val

    async def listen_for_pulse(self):
        while True:
            raw, hpf = self.sample_hpf()
            if hpf > self._edge_threshold:
                start_time = ticks_us()
                break
            await asyncio.sleep(0) 
            
        peak_amplitude = 0
        
        while True:
            raw, hpf = self.sample_hpf()
            if raw > peak_amplitude:
                peak_amplitude = raw
            if hpf < -self._edge_threshold:
                end_time = ticks_us()
                break
            await asyncio.sleep(0)

        duration = ticks_diff(end_time, start_time)
        return duration, peak_amplitude

    async def run(self):
        print("Robot Receiver Started on Core 0 (Async Mode).")
        
        # State variables for assembling characters and strings
        reading_bits = False
        current_byte = 0
        bit_count = 0
        message_buffer = ""

        try:
            while True:
                duration_us, peak_amplitude = await self.listen_for_pulse()
                duration_ms = duration_us / 1000.0
                
                # Check for Start Pulse (40-60ms)
                if 40 < duration_ms < 60:
                    reading_bits = True
                    current_byte = 0
                    bit_count = 0
                    
                # If we are reading bits, parse 1s and 0s
                elif reading_bits:
                    bit_val = -1
                    if 20 < duration_ms < 30:
                        bit_val = 1
                    elif 5 < duration_ms < 15:
                        bit_val = 0
                    
                    if bit_val != -1:
                        # Shift the bit into place (Transmitter sends LSB first)
                        current_byte |= (bit_val << bit_count)
                        bit_count += 1
                        
                        # We have collected a full 8-bit character
                        if bit_count == 8:
                            char = chr(current_byte)
                            
                            # End of message
                            if char == '\n':
                                if message_buffer.strip(): # Ignore completely empty payloads
                                    print(f"Full message received: {message_buffer}")
                                    
                                    # Update shared data
                                    with data_lock:
                                        shared_data["messages"].append(message_buffer)
                                        if len(shared_data["messages"]) > MAX_MESSAGES:
                                            shared_data["messages"].pop(0)
                                
                                # Clear the buffer for the next message
                                message_buffer = ""
                            else:
                                # Append character to buffer
                                message_buffer += char
                                
                            # Reset bit reader state machine, wait for next START PULSE
                            reading_bits = False 
                            
        except asyncio.CancelledError:
            print("Receiver task cancelled.")
        except Exception as e:
            print(f"Receiver error: {e}")

async def main():
    receiver_adc = ADC(0) 
    test_robot = SingleDiodeRobot(receiver_adc=receiver_adc, edge_threshold=2000)
    await test_robot.run()

if __name__ == "__main__":
    # 1. Connect to Network (Fill in your credentials)
    connect_wifi("", "")
    
    # 2. Start HTTP server on Core 1
    _thread.start_new_thread(core1_http_server, ())
    
    # 3. Start Async loop on Core 0
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProgram interrupted by user.")