import Navbar from "./components/Navbar";
import Hero from "./components/Hero";
import Features from "./components/Features";
import Pipeline from "./components/Pipeline";
import Quickstart from "./components/Quickstart";
import Architecture from "./components/Architecture";
import Audiences from "./components/Audiences";
import { Ecosystem, DocsCTA, Footer } from "./components/Closing";
export default function App() {
  return (
    <div id="top" className="min-h-screen overflow-x-hidden">
      <Navbar />
      <main>
        <Hero />
        <Features />
        <Pipeline />
        <Quickstart />
        <Architecture />
        <Audiences />
        <Ecosystem />
        <DocsCTA />
      </main>
      <Footer />
    </div>
  );
}
