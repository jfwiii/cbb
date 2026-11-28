import "./globals.css";
import Nav from "@/components/Nav";

export const metadata = {
  title: "jfwiii.net College Basketball",
  description: "Ratings, four factors and a nightly Watch Score for every D1, D2 and D3 men's game.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Serif+Display&family=DM+Mono:wght@400;500&family=Outfit:wght@300;400;500;600;700&display=swap"
        />
      </head>
      <body>
        <header className="site">
          <div>
            <div className="ey">jfwiii.net · Men's College Basketball</div>
            <div className="brand">College <em>Basketball</em></div>
          </div>
        </header>
        <Nav />
        <main>{children}</main>
        <footer className="site">
          <span>Data: ncaa.com (via henrygd/ncaa-api), ESPN · updated nightly</span>
          <a href="/status/">Data status</a>
          <a href="/methodology/">Methodology</a>
          <a href="https://jfwiii.github.io/cbb/index.json">Downloads</a>
        </footer>
      </body>
    </html>
  );
}
