from machine import Pin, ADC, UART
from utime import ticks_ms, ticks_diff
from geortzel import Goertzel

class SingleDiodeRobot:
    def __init__(self, receiver_adc: ADC, bit_rate: float = 250., threshold: int = 2500):
        self._receiver_adc: ADC = receiver_adc

        self._threshold: int = threshold

        self._bit_rate: float = bit_rate
        self._bit_period_us = int(1_000_000 / bit_rate)

    def wait_for_start_bit(self):


    # -------------------------------
    # SENSING
    # -------------------------------
    def sample_bit(self) -> int:
        value = self._receiver_adc.read_u16()

        if value > self._threshold:
            return 1
        
        return 0

    def step(self):
        # Sample sensors
        samples = self.sample_diodes()
        imu = self.read_imu()

        # Store
        for i, s in enumerate(samples):
            self._adc_buffer[i].append(s)

        self._imu_latest = imu

        if len(self._adc_buffer[0]) >= self._frame_size:
            features = self.process_frame()
            state = self.fuse_state(features, imu)
            action = self.policy(state)
            self.actuate(action)

            self.clear_buffers()

        
    # -------------------------------
    # SIGNAL PROCESSING
    # -------------------------------
    def process_frame(self) -> list:
        results = []

        for diode_samples in self._adc_buffer:
            diode_results = []

            for f in self._near_beacon_frequencies:
                g = Goertzel(f, self._sample_rate_hz, n=len(diode_samples))
                power = g.compute(diode_samples)
                diode_results.append(power)

            results.append(diode_results)

        return results
    
    def fuse_state(self, features: list, imu):
        return {}
    
    def policy(self, state):
        return {}
    
    def actuate(self, action):
        pass
        
    def clear_buffers(self):
        self._adc_buffer = [[] for _ in self._receiver_adcs]

    
if __name__ == "__main__":
    imu_uart: UART = UART(0, baudrate=9600, tx=Pin(0), rx=Pin(1))
    led_pin: Pin = Pin(16)
    receiver_adcs: list = [
        ADC(0), ADC(1), ADC(2)
    ]
    near_beacon_frequencies: list = [
        250
    ]

    test_robot: SingleDiodeRobot = SingleDiodeRobot(
        receiver_adcs=receiver_adcs,
        led_gpio=led_pin,
        imu_uart=imu_uart,
        near_beacon_frequencies=near_beacon_frequencies,
        sample_rate_hz=2500)

    test_robot.run()