from machine import Pin, ADC, UART
from utime import ticks_ms, ticks_diff
from geortzel import Goertzel

class Robot:
    def __init__(self, receiver_adcs: list, led_gpio: Pin, imu_uart: UART,
                 near_beacon_frequencies: list, sample_rate_hz: float = 100e3,
                 frame_size: int = 128):
        self._receiver_adcs: list = receiver_adcs
        self._led_gpio: Pin = led_gpio
        self._imu_uart: UART = imu_uart
        self._near_beacon_frequencies: list = near_beacon_frequencies

        # ---- runtime buffers ----
        self._adc_buffer = [[] for _ in receiver_adcs]
        self._imu_latest = None
        self._frame_size: int = frame_size

        # Control timing
        self._sample_rate_hz: float = sample_rate_hz
        self._dt = 1. / self._sample_rate_hz

    def run(self) -> None:
        # Read from diode ADCs
        last_t = ticks_ms()

        while True:
            now = ticks_ms()

            if ticks_diff(now, last_t) >= self._dt * 1000:
                last_t = now
                self.step()

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
    # SENSING
    # -------------------------------
    def sample_bit(self):
        value = self._receiver_adcs[0].read_u16()

        if value > self._threshold:
            return 1
        return 0

    def sample_diodes(self) -> list:
        # This is on hold until we move to more than one diode
        return [adc.read_u16() for adc in self._receiver_adcs]

    def read_imu(self) -> str | None:
        if self._imu_uart.any():
            return self._imu_uart.readline()
        
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

    test_robot: Robot = Robot(receiver_adcs=receiver_adcs,
                              led_gpio=led_pin,
                              imu_uart=imu_uart,
                              near_beacon_frequencies=near_beacon_frequencies,
                              sample_rate_hz=2500)
    
    test_robot.run()