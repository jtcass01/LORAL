# Sequence Diagrams

## Robot Localization
```
title Robot Localization Sequence (Active Sensing + IMU Fusion)

participant RPI Pico 2W
participant PhotodiodeArray
participant BNO085_IMU
participant Servo
participant Goertzel
participant Localization
participant CalibrationModel
participant RL

loop Dwell Period (T seconds)

    RPI Pico 2W->PhotodiodeArray: Sample IR signals
    PhotodiodeArray-->RPI Pico 2W: ADC samples [D1, D2, D3]

    RPI Pico 2W->BNO085_IMU: Read orientation estimate
    BNO085_IMU-->RPI Pico 2W: IMU yaw θ_IMU

    RPI Pico 2W->Goertzel: Extract frequency magnitudes
    Goertzel-->RPI Pico 2W: Signal matrix [freq × diode]

    RPI Pico 2W->Localization: Visible tower IDs
    Localization-->RPI Pico 2W: Tower set

end

RPI Pico 2W->CalibrationModel: Estimate bearing θ_IR from intensity pattern
CalibrationModel-->RPI Pico 2W: θ_IR

RPI Pico 2W->Localization: Fuse (θ_IR + θ_IMU + tower IDs)
Localization-->RPI Pico 2W: Position estimate (x, y, θ)

RPI Pico 2W->RL: State (x, y, θ, tower set)
RL-->RPI Pico 2W: Action (Δθ for sensing improvement)

RPI Pico 2W->Servo: Reorient to improve signal quality
Servo-->RPI Pico 2W: Updated orientation
```

## Tower Beacon
```
title Tower Beacon Operation

participant Tower
participant Beacon

loop Continuous Operation
    Tower->Beacon: Transmit(ID, modulated at assigned frequency)
end
```