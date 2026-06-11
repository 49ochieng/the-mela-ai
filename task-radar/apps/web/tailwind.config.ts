import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Brand
        navy: {
          deep: "#071A33",
          DEFAULT: "#0B1B33",
          900: "#071A33",
          800: "#0B1B33",
          700: "#102649",
          600: "#1A3463",
        },
        brand: {
          DEFAULT: "#1E5BD6",     // primary blue
          bright: "#3DA5FF",      // bright/electric
          50: "#EEF4FF",
          100: "#DBE7FF",
          200: "#B6CFFF",
          500: "#1E5BD6",
          600: "#1849B0",
          700: "#143A8C",
        },
        // Surface
        canvas: "#F6F8FC",         // soft background
        surface: "#FFFFFF",        // cards
        hairline: "#E5EAF2",       // borders
        // Text
        ink: "#08111F",            // primary
        muted: "#667085",          // secondary
        subtle: "#98A2B3",         // tertiary
        // Status
        success: "#12B76A",
        warning: "#F79009",
        danger:  "#F04438",
        // Aliases (back-compat)
        mela: {
          navy: "#0B1B33",
          blue: "#1E5BD6",
          accent: "#3DA5FF",
          ink: "#08111F",
          muted: "#667085",
          surface: "#F6F8FC",
          border: "#E5EAF2",
        },
      },
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        display: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      fontSize: {
        "display-2xl": ["3.25rem", { lineHeight: "1.1", letterSpacing: "-0.025em" }],
        "display-xl":  ["2.5rem",  { lineHeight: "1.15", letterSpacing: "-0.022em" }],
        "display-lg":  ["2rem",    { lineHeight: "1.2",  letterSpacing: "-0.02em"  }],
      },
      borderRadius: {
        xl: "14px",
        "2xl": "18px",
        "3xl": "24px",
      },
      boxShadow: {
        soft: "0 1px 2px rgba(8, 17, 31, 0.04), 0 1px 3px rgba(8, 17, 31, 0.06)",
        card: "0 1px 2px rgba(8, 17, 31, 0.04), 0 4px 16px rgba(8, 17, 31, 0.05)",
        lift: "0 8px 28px rgba(8, 17, 31, 0.10)",
        ring: "0 0 0 4px rgba(30, 91, 214, 0.12)",
      },
      backgroundImage: {
        "navy-gradient": "linear-gradient(180deg, #0B1B33 0%, #071A33 100%)",
        "brand-gradient": "linear-gradient(135deg, #1E5BD6 0%, #3DA5FF 100%)",
        "hero-glow": "radial-gradient(60% 80% at 50% 0%, rgba(61,165,255,0.18) 0%, rgba(11,27,51,0) 60%)",
      },
    },
  },
  plugins: [],
};
export default config;

