module.exports = {
  content: ["./src/**/*.{js,jsx}", "./public/index.html"],
  theme: {
    extend: {
      colors: {
        primary: { DEFAULT: "#002FA7", hover: "#00227A" },
        ink: "#09090B",
        muted: "#71717A",
        line: "#E4E4E7",
        surface: "#F4F4F5",
      },
      fontFamily: {
        sans: ["'IBM Plex Sans'", "'Noto Sans SC'", "sans-serif"],
        mono: ["'IBM Plex Mono'", "monospace"],
      },
    },
  },
  plugins: [require("@tailwindcss/typography")],
};
