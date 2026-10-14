"use client"

import type React from "react"
import { useMemo } from "react"

const CENTER = 200
const PETALS = 8

function petalPath(rin: number, rout: number, width: number) {
  const rmid = (rin + rout) / 2
  return `M 0,${rin} Q ${-width},${rmid} 0,${rout} Q ${width},${rmid} 0,${rin} Z`
}

function spiralArm(startAngle: number, turns: number, r0: number, b: number, steps = 60) {
  const pts: string[] = []
  const total = turns * Math.PI * 2
  for (let i = 0; i <= steps; i++) {
    const t = (i / steps) * total
    const r = r0 * Math.exp(b * t)
    const a = startAngle + t
    const x = CENTER + r * Math.cos(a)
    const y = CENTER + r * Math.sin(a)
    pts.push(`${i === 0 ? "M" : "L"} ${x.toFixed(2)},${y.toFixed(2)}`)
  }
  return pts.join(" ")
}

interface WatchtowerLogoProps {
  size?: number | string
  hideWordmark?: boolean
}

export default function WatchtowerLogo({ size = "min(80vw,520px)", hideWordmark = false }: WatchtowerLogoProps) {
  const petalAngles = useMemo(
    () => Array.from({ length: PETALS }, (_, i) => (360 / PETALS) * i),
    [],
  )

  const ringNodes = useMemo(() => {
    const nodes: { x: number; y: number; r: number }[] = []
    const count = 16
    for (let i = 0; i < count; i++) {
      const a = (Math.PI * 2 * i) / count
      const radius = i % 2 === 0 ? 70 : 86
      nodes.push({
        x: CENTER + radius * Math.cos(a),
        y: CENTER + radius * Math.sin(a),
        r: i % 2 === 0 ? 3.2 : 2.2,
      })
    }
    return nodes
  }, [])

  const spiralArms = useMemo(
    () => Array.from({ length: 6 }, (_, i) => (Math.PI * 2 * i) / 6),
    [],
  )

  const svgSize = typeof size === "number" ? `${size}px` : size

  return (
    <>
      <style>{`
        @keyframes gl-spin {
          to { transform: rotate(360deg); }
        }
        @keyframes gl-breathe {
          0%, 100% { opacity: 0.55; transform: scale(1); }
          50%       { opacity: 1;    transform: scale(1.08); }
        }
        @keyframes gl-pulse {
          0%, 100% { opacity: 0.45; }
          50%       { opacity: 1; }
        }
        @keyframes gl-text-glow {
          0%, 100% {
            text-shadow:
              0 0 8px rgba(45,212,191,0.45),
              0 0 22px rgba(34,211,238,0.3);
          }
          50% {
            text-shadow:
              0 0 14px rgba(45,212,191,0.75),
              0 0 34px rgba(34,211,238,0.55);
          }
        }
      `}</style>

      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <svg
          viewBox="0 0 400 400"
          style={{
            width: svgSize,
            height: svgSize,
            filter: "drop-shadow(0 0 30px rgba(45,212,191,0.25))",
          }}
          role="img"
          aria-label="THE WATCHTOWER animated emblem"
        >
          <defs>
            <radialGradient id="gl-coreGlow" cx="50%" cy="50%" r="50%">
              <stop offset="0%"   stopColor="#2dd4bf" stopOpacity="0.45" />
              <stop offset="60%"  stopColor="#22d3ee" stopOpacity="0.08" />
              <stop offset="100%" stopColor="#22d3ee" stopOpacity="0" />
            </radialGradient>
            <filter id="gl-soft" x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="1.4" result="b" />
              <feMerge>
                <feMergeNode in="b" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>

          {/* ambient core glow */}
          <circle
            cx={CENTER}
            cy={CENTER}
            r="150"
            fill="url(#gl-coreGlow)"
            style={{
              animation: "gl-breathe 5s ease-in-out infinite",
              transformOrigin: "200px 200px",
            }}
          />

          {/* Outer petal mandala — slow rotation */}
          <g
            filter="url(#gl-soft)"
            style={{
              animation: "gl-spin 46s linear infinite",
              transformOrigin: "200px 200px",
            }}
          >
            {petalAngles.map((deg, i) => (
              <g key={deg} transform={`rotate(${deg} ${CENTER} ${CENTER})`}>
                <g transform={`translate(${CENTER} ${CENTER})`}>
                  <path d={petalPath(60, 150, 34)} fill="none" stroke="#3b82f6" strokeWidth="2"   opacity="0.85" />
                  <path d={petalPath(60, 150, 20)} fill="none" stroke="#22d3ee" strokeWidth="1.2" opacity="0.55" />
                  <circle
                    cx="0" cy="150" r="4.5"
                    fill="#22d3ee"
                    style={{ animation: `gl-pulse 2.4s ease-in-out ${i * 0.18}s infinite` }}
                  />
                  <circle
                    cx={-34} cy={105} r="3"
                    fill="#38bdf8"
                    style={{ animation: `gl-pulse 2.4s ease-in-out ${i * 0.18 + 0.4}s infinite` }}
                  />
                  <circle
                    cx={34} cy={105} r="3"
                    fill="#38bdf8"
                    style={{ animation: `gl-pulse 2.4s ease-in-out ${i * 0.18 + 0.6}s infinite` }}
                  />
                  <circle cx="0" cy="60" r="3.4" fill="#3b82f6" />
                </g>
              </g>
            ))}
          </g>

          {/* Inner ring — counter rotation */}
          <g
            style={{
              animation: "gl-spin 36s linear infinite reverse",
              transformOrigin: "200px 200px",
            }}
          >
            {ringNodes.map((n, i) => (
              <circle
                key={i}
                cx={n.x}
                cy={n.y}
                r={n.r}
                fill="#22d3ee"
                style={{ animation: `gl-pulse 2.4s ease-in-out ${i * 0.1}s infinite` }}
              />
            ))}
          </g>

          {/* Central spiral — faster counter rotation */}
          <g
            filter="url(#gl-soft)"
            style={{
              animation: "gl-spin 14s linear infinite reverse",
              transformOrigin: "200px 200px",
            }}
          >
            {spiralArms.map((a, i) => (
              <path
                key={i}
                d={spiralArm(a, 0.62, 8, 0.45)}
                fill="none"
                stroke={i % 2 === 0 ? "#2dd4bf" : "#22d3ee"}
                strokeWidth="3"
                strokeLinecap="round"
                opacity="0.9"
              />
            ))}
            {Array.from({ length: 7 }).map((_, i) => {
              const a = (Math.PI * 2 * i) / 7
              const r = 18 + (i % 3) * 6
              return (
                <circle
                  key={`d${i}`}
                  cx={CENTER + r * Math.cos(a)}
                  cy={CENTER + r * Math.sin(a)}
                  r="2.4"
                  fill="#2dd4bf"
                  style={{ animation: `gl-pulse 2.4s ease-in-out ${i * 0.15}s infinite` }}
                />
              )
            })}
          </g>
        </svg>

        {!hideWordmark && (
          <div style={{ marginTop: "-8px", textAlign: "center" }}>
            <h1
              style={{
                fontFamily: "Inter, sans-serif",
                fontSize: "clamp(2rem, 5vw, 3.5rem)",
                fontWeight: 300,
                letterSpacing: "0.18em",
                color: "#22d3ee",
                animation: "gl-text-glow 4s ease-in-out infinite",
                margin: 0,
              }}
            >
              THE WATCHTOWER
            </h1>
            <p
              style={{
                marginTop: "12px",
                fontFamily: "monospace",
                fontSize: "0.85rem",
                letterSpacing: "0.5em",
                color: "#3b82f6",
              }}
            >
              SINCE 2026
            </p>
          </div>
        )}
      </div>
    </>
  )
}
