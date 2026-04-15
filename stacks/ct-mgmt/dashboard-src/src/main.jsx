import { hydrate, render } from 'preact';
import App from './App';
import './index.css';

const root = document.getElementById('root');
const initial = window.__INITIAL_DATA__;

// Hydrate if server rendered, otherwise plain render
if (root.hasChildNodes()) {
  hydrate(<App initial={initial} />, root);
} else {
  render(<App initial={initial} />, root);
}
