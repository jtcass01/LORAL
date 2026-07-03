import matplotlib.pyplot as plt
import csv

time = []
raw = []
filtered = []

with open("adc_log.csv", "r") as f:
    reader = csv.DictReader(f)

    for row in reader:
        time.append(int(row["time_ms"]))
        raw.append(int(row["raw"]))
        filtered.append(int(row["filtered"]))

plt.plot(time, raw, label="Raw ADC")
# plt.plot(time, filtered, label="Filtered (High-pass)")

plt.xlabel("Time (ms)")
plt.ylabel("ADC Value")
plt.title("Photodiode Signal: Raw")
plt.legend()
plt.grid(True)

plt.show()