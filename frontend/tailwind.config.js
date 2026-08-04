/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,jsx}"
  ],
  theme: {
    extend: {
      colors: {
        lime: {
          DEFAULT: "#00ff80",
          400: "#00ff80"
        }
      }
    }
  },
  plugins: []
};