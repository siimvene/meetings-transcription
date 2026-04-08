import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        surface: {
          DEFAULT: "#1a1a2e",
          light: "#222240",
          lighter: "#2a2a4a",
        },
        accent: {
          DEFAULT: "#4f8cff",
          hover: "#6ba0ff",
        },
      },
    },
  },
  plugins: [],
};

export default config;
