// Dev server for the demo page: forwards /api to the backend, so the page and the API share an origin.
// API_URL overrides the backend address; allowedHosts lets the HTTPS tunnel's hostname through.
export default {
  server: {
    host: true,
    port: 5173,
    allowedHosts: true,
    proxy: {
      "/api/": { target: process.env.API_URL || "http://127.0.0.1:8000" },
    },
  },
};
