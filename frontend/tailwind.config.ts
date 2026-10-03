import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Noto Sans SC", "Source Han Sans SC", "Microsoft YaHei", "sans-serif"],
      },
      colors: {
        ink: "#172033",
        paper: "#f6f4ef",
        line: "#d8d2c5",
        signal: "#b42318",
        steel: "#23536f",
      },
      boxShadow: {
        panel: "0 18px 60px rgba(23, 32, 51, 0.12)",
      },
    },
  },
  plugins: [],
} satisfies Config;
