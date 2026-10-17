"""Synthetic measurement profiles, separate from declared physical demand."""

import random

from pydantic import Field, model_validator

from shipos.sim.timeline import Payload, tick_time


class Pulse(Payload):
    start_s: float
    end_s: float
    multiplier: float = Field(ge=0)

    @model_validator(mode="after")
    def interval(self):
        tick_time(self.start_s)
        tick_time(self.end_s)
        if self.end_s <= self.start_s:
            raise ValueError("pulse end_s must be after start_s")
        return self


class Profile(Payload):
    noise_fraction: float = Field(default=0.0, ge=0, le=1)
    pulses: list[Pulse] = Field(default_factory=list)

    def measure(self, nominal_kw: float, t_s: float, rng: random.Random) -> float:
        multiplier = 1.0
        for pulse in self.pulses:
            if pulse.start_s <= t_s < pulse.end_s:
                multiplier *= pulse.multiplier
        noise = rng.uniform(-self.noise_fraction, self.noise_fraction) if self.noise_fraction else 0
        return nominal_kw * multiplier * (1 + noise)
