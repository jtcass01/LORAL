from machine import ADC
from utime import sleep_ms

adc = ADC(0)

while True:
    value = adc.read_u16()
    print(value)
    sleep_ms(50)