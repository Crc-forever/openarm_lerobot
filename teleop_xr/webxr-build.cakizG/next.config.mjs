/** @type {import('next').NextConfig} */
const nextConfig = {
	output: "export",
	distDir: process.env.NEXT_DIST_DIR || ".next",
	// Optional: If you have existing code that relies on window/document in
	// global scope during build reactStrictMode: true,
};

export default nextConfig;
