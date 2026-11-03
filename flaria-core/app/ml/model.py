"""CrossAttentionFusion: visual queries attend over acoustic keys/values.

Inputs are per-patient z-scored sequences
    audio  (B, T, 15)   vision (B, T, 5)
Output is one logit per sequence (B, 1); sigmoid gives the anomaly index.

Why cross-attention with vision as the query: during healthy speech, lip
aperture and acoustic energy/spectral shape move together with a small, stable
lag. Each visual time step asks "which acoustic moment explains me?". When
coordination degrades (bulbar weakness, facial palsy, respiratory effort) the
attended acoustic context stops matching the visual state. That residual
``v - ctx`` is pooled explicitly alongside the fused representation so the head
sees incoordination directly rather than having to rediscover it.

Only imported by training/export scripts; the API server runs the exported
ONNX graph and does not depend on PyTorch.
"""
from __future__ import annotations

import math

import torch
from torch import nn


class SinusoidalPositionalEncoding(nn.Module):
    def __init__(self, d_model: int, max_len: int = 512) -> None:
        super().__init__()
        position = torch.arange(max_len, dtype=torch.float32).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2, dtype=torch.float32) * (-math.log(10000.0) / d_model))
        pe = torch.zeros(1, max_len, d_model)
        pe[0, :, 0::2] = torch.sin(position * div_term)
        pe[0, :, 1::2] = torch.cos(position * div_term)
        self.register_buffer("pe", pe, persistent=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + self.pe[:, : x.size(1)]


def _projector(in_dim: int, d_model: int, dropout: float) -> nn.Sequential:
    return nn.Sequential(nn.Linear(in_dim, d_model), nn.GELU(), nn.Dropout(dropout), nn.Linear(d_model, d_model))


class CrossAttentionFusion(nn.Module):
    def __init__(
        self,
        audio_dim: int = 15,
        vision_dim: int = 5,
        d_model: int = 32,
        n_heads: int = 4,
        ff_mult: int = 4,
        dropout: float = 0.1,
        max_len: int = 512,
    ) -> None:
        super().__init__()
        if d_model % n_heads:
            raise ValueError("d_model must be divisible by n_heads")
        self.audio_proj = _projector(audio_dim, d_model, dropout)
        self.vision_proj = _projector(vision_dim, d_model, dropout)
        self.pos = SinusoidalPositionalEncoding(d_model, max_len)
        self.norm_q = nn.LayerNorm(d_model)
        self.norm_kv = nn.LayerNorm(d_model)
        self.cross_attn = nn.MultiheadAttention(d_model, n_heads, dropout=dropout, batch_first=True)
        self.norm_ff = nn.LayerNorm(d_model)
        self.ffn = nn.Sequential(
            nn.Linear(d_model, d_model * ff_mult), nn.GELU(), nn.Dropout(dropout),
            nn.Linear(d_model * ff_mult, d_model), nn.Dropout(dropout),
        )
        self.pool_score = nn.Linear(d_model, 1)
        self.head = nn.Sequential(
            nn.LayerNorm(2 * d_model), nn.Linear(2 * d_model, d_model), nn.GELU(), nn.Dropout(dropout),
            nn.Linear(d_model, 1),
        )

    def forward(self, audio: torch.Tensor, vision: torch.Tensor) -> torch.Tensor:
        a = self.pos(self.audio_proj(audio))
        v = self.pos(self.vision_proj(vision))
        kv = self.norm_kv(a)
        ctx, _ = self.cross_attn(self.norm_q(v), kv, kv, need_weights=False)
        h = v + ctx
        h = h + self.ffn(self.norm_ff(h))
        weights = torch.softmax(self.pool_score(h), dim=1)            # (B, T, 1)
        pooled = (weights * h).sum(dim=1)                               # (B, D)
        incoordination = (v - ctx).abs().mean(dim=1)                    # (B, D)
        return self.head(torch.cat([pooled, incoordination], dim=-1))   # (B, 1)
