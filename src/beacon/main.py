from machine import Pin
from utime import sleep

led = Pin(16, Pin.OUT)

while True:
    led.on()
    print("LED ON")
    sleep(1)

    led.off()
    print("LED OFF")
    sleep(1)