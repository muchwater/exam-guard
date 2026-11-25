"""802.11 air time for 2.4 GHz: HT 20 MHz single-stream data PPDUs, non-HT OFDM control responses.

Formulas follow Linux ath9k (xmit.c ath_pkt_duration, mac.c ath9k_hw_computetxtime):
    HT PPDU   = HT preamble + ceil((8*L + 22) / N_DBPS) * 4 us
    OFDM PPDU = 20 us       + ceil((8*L + 22) / N_DBPS) * 4 us
Constants come from configs (synth.netsim.phy) with their sources.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Phy:
    slot_us: float
    sifs_us: float
    ofdm_preamble_us: float
    symbol_us: float
    plcp_bits: int
    ht_preamble_us: float
    ht_bits_per_symbol: tuple[int, ...]
    mandatory_ofdm_mbps: tuple[float, ...]
    ack_bytes: int
    blockack_bytes: int
    fcs_bytes: int
    ampdu_delimiter_bytes: int
    ppdu_max_us: float
    ampdu_max_bytes: int

    @classmethod
    def from_config(cls, c: dict) -> "Phy":
        return cls(
            slot_us=float(c["slot_us"]), sifs_us=float(c["sifs_us"]),
            ofdm_preamble_us=float(c["ofdm_preamble_us"]), symbol_us=float(c["symbol_us"]),
            plcp_bits=int(c["plcp_bits"]), ht_preamble_us=float(c["ht_preamble_us"]),
            ht_bits_per_symbol=tuple(int(x) for x in c["ht_bits_per_symbol"]),
            mandatory_ofdm_mbps=tuple(float(x) for x in c["mandatory_ofdm_mbps"]),
            ack_bytes=int(c["ack_bytes"]), blockack_bytes=int(c["blockack_bytes"]),
            fcs_bytes=int(c["fcs_bytes"]), ampdu_delimiter_bytes=int(c["ampdu_delimiter_bytes"]),
            ppdu_max_us=float(c["ppdu_max_us"]), ampdu_max_bytes=int(c["ampdu_max_bytes"]),
        )

    # --- rates -----------------------------------------------------------------------------------
    def ht_rate_mbps(self, mcs: int) -> float:
        return self.ht_bits_per_symbol[mcs] / self.symbol_us

    def response_rate_mbps(self, mcs: int) -> float:
        """Control response rate: highest mandatory OFDM rate not above the eliciting data rate."""
        r = self.ht_rate_mbps(mcs)
        ok = [m for m in self.mandatory_ofdm_mbps if m <= r]
        return max(ok) if ok else min(self.mandatory_ofdm_mbps)

    # --- durations (microseconds) ----------------------------------------------------------------
    def _nsym(self, nbytes: int, bits_per_symbol: float) -> int:
        return math.ceil((8 * nbytes + self.plcp_bits) / bits_per_symbol)

    def ht_ppdu_us(self, psdu_bytes: int, mcs: int) -> float:
        return self.ht_preamble_us + self._nsym(psdu_bytes, self.ht_bits_per_symbol[mcs]) * self.symbol_us

    def ofdm_ppdu_us(self, psdu_bytes: int, mbps: float) -> float:
        return self.ofdm_preamble_us + self._nsym(psdu_bytes, mbps * self.symbol_us) * self.symbol_us

    def ack_us(self, mcs: int) -> float:
        return self.ofdm_ppdu_us(self.ack_bytes, self.response_rate_mbps(mcs))

    def blockack_us(self, mcs: int) -> float:
        return self.ofdm_ppdu_us(self.blockack_bytes, self.response_rate_mbps(mcs))

    def aifs_us(self, aifsn: int) -> float:
        return self.sifs_us + aifsn * self.slot_us

    # --- A-MPDU ----------------------------------------------------------------------------------
    def ampdu_layout(self, mpdu_bytes: list[int]) -> tuple[int, list[int]]:
        """PSDU length of an A-MPDU and the byte offset at which each subframe's MPDU ends.

        mpdu_bytes include the FCS. Every subframe = delimiter + MPDU, padded to 4 bytes except the last.
        """
        off, ends = 0, []
        for i, n in enumerate(mpdu_bytes):
            off += self.ampdu_delimiter_bytes + n
            ends.append(off)
            if i != len(mpdu_bytes) - 1:
                off += (-off) % 4
        return off, ends

    def subframe_end_us(self, byte_end: int, mcs: int) -> float:
        """Time from PPDU start until the last bit of the byte at offset byte_end is on air."""
        return self.ht_preamble_us + self._nsym(byte_end, self.ht_bits_per_symbol[mcs]) * self.symbol_us

    def fits_ampdu(self, mpdu_bytes: list[int], mcs: int) -> bool:
        psdu, _ = self.ampdu_layout(mpdu_bytes)
        return psdu <= self.ampdu_max_bytes and self.ht_ppdu_us(psdu, mcs) <= self.ppdu_max_us
