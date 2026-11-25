// Rebuild from the project root with the command below.
// npm exec --package=tailwindcss@3.4.17 -- tailwindcss -c tools/dashboard-tailwind.cjs -o app/static/dashboard.css --minify
const color = name => "rgb(var(--" + name + "-rgb) / <alpha-value>)";

module.exports = {
      content: ['./app/ui.py', './app/templates/privacy.html', './app/templates/terms.html'],
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            brand: { 300: color('accent-hover'), 400: color('accent'), 500: color('accent'), 600: color('accent-hover') },
            dark: { 950: color('canvas'), 900: color('surface'), 850: color('raised'), 800: color('raised') },
            slate: { 100: color('text'), 200: '#e8dfe9', 300: color('secondary'), 400: '#bdb1c1', 500: '#b5a7b9', 600: '#a69aa9', 700: '#514656', 800: color('raised'), 900: color('surface'), 950: color('canvas') },
          },
          fontFamily: {
            sans: ['Segoe UI', 'Arial', 'sans-serif'],
            display: ['Barlow Condensed', 'Arial Narrow', 'sans-serif'],
            mono: ['Consolas', 'monospace'],
          }
        }
      }
    };
