/** @type {import('next').NextConfig} */
const nextConfig = {
  // The dashboard is a single-operator surface holding job-search telemetry.
  // It must never be indexed, and the header is set here as well as in
  // middleware so that static assets carry it too.
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [{ key: "X-Robots-Tag", value: "noindex, nofollow, noarchive" }],
      },
    ];
  },
};

export default nextConfig;
