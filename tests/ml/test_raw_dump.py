"""Feed the raw-stream analyser each realistic failure mode."""
import sys, struct
sys.path.insert(0, 'src/ml')
import collect_training_data as C

def case(title, blob):
    print("=" * 78)
    print(f"CASE: {title}")
    print("=" * 78)
    C._analyse_raw(blob)
    print()

# 1. device silent
case("Pico not running the logger at all", b"")

# 2. logger crashed - stale single_diode_robot.py on the device
tb = (b"Traceback (most recent call last):\r\n"
      b'  File "serial_pulse_logger.py", line 57, in main\r\n'
      b"AttributeError: 'SingleDiodeRobot' object has no attribute 'detector_state'\r\n")
case("Logger crashed (old single_diode_robot.py on device)", tb)

# 3. old robot main.py auto-running instead
case("Wrong program auto-running at boot",
     b"Connecting to WiFi...\r\nConnected! IP Address: 192.168.1.42\r\n"
     b"Robot Receiver Started on Core 0 (Async Mode).\r\n"
     b"Starting HTTP Server on Core 1...\r\nFull message received: HELLO\r\n")

# 4. old 15-byte firmware still on the device
old = b"".join(struct.pack("<BIHHHi", 0xAA, 25100, 43440, 42000, 42000, 2880) for _ in range(8))
case("Stale 15-byte firmware", old)

# 5. the current TEXT protocol, captured mid-run so there is no banner.
#    Previously this case built a binary packet; the wire format is text now,
#    and the analyser must recognise it from the records rather than the banner.
mid_run = (b"IDLE thr=1136 a_raw=41194 b_raw=41002 bo=0 pulses=294 since_ms=469\r\n"
           + b"".join(
               b"P dur=25110 gap=10050 a_hpf=1887 b_hpf=1204 "
               b"a_pk=42218 b_pk=41902 thr=1078 bo=0\r\n" for _ in range(8)))
case("Current text protocol, captured mid-run (no banner)", mid_run)

# 6. the same protocol with the boot banner present
with_banner = b"LORAL 6 dual_comms_logger ready adc_a=0 adc_b=1\r\n" + mid_run
case("Current text protocol, banner present", with_banner)
