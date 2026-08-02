from machine import Pin, ADC
from utime import ticks_us, ticks_diff, sleep_us

class SingleDiodeRobot:
    def __init__(self, receiver_adc: ADC, edge_threshold: int = 2000):
        self._adc: ADC = receiver_adc
        self._edge_threshold: int = edge_threshold
        
        # HPF State variables
        self._prev_raw: int = 0
        self._filtered_val: float = 0.0
        self._alpha: float = 0.85

    def sample_hpf(self) -> tuple[int, float]:
        """Synchronous fast math: samples ADC and updates filter."""
        raw = self._adc.read_u16()
        
        # Digital HPF (DC Blocker)
        self._filtered_val = (raw - self._prev_raw) + (self._alpha * self._filtered_val)
        self._prev_raw = raw
        
        return raw, self._filtered_val

    def listen_for_pulse(self):
        """Synchronously waits for an IR pulse, blocking the Pico."""
        
        # 1. Wait for RISING EDGE
        while True:
            raw, hpf = self.sample_hpf()
            if hpf > self._edge_threshold:
                start_time = ticks_us()
                break
            
            # Blocking sleep
            sleep_us(100) 
            
        peak_amplitude = 0
        
        # 2. Wait for FALLING EDGE
        while True:
            raw, hpf = self.sample_hpf()
            
            if raw > peak_amplitude:
                peak_amplitude = raw
                
            if hpf < -self._edge_threshold:
                end_time = ticks_us()
                break
                
            # Blocking sleep
            sleep_us(100)

        duration = ticks_diff(end_time, start_time)
        return duration, peak_amplitude

    def run(self):
        """Main loop for the receiver."""
        print("Robot Receiver Started (Sync/Blocking Mode).")
        
        while True:
            # Block until pulse detection
            duration_us, peak_amplitude = self.listen_for_pulse()
            duration_ms = duration_us / 1000.0
            
            if 40 < duration_ms < 60:
                print(f"START PULSE detected! Peak Amplitude: {peak_amplitude}")
            elif 20 < duration_ms < 30:
                print("Bit: 1")
            elif 5 < duration_ms < 15:
                print("Bit: 0")
            else:
                pass # Ignore tiny noise spikes or malformed pulses


def main():
    # Setup hardware
    receiver_adc = ADC(0) 
    test_robot = SingleDiodeRobot(receiver_adc=receiver_adc, edge_threshold=2000)
    
    # Run the receiver loop
    test_robot.run()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nProgram interrupted by user.")