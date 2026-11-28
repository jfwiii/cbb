/** Static export: Netlify serves plain files; data is fetched in the browser. */
module.exports = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
};
