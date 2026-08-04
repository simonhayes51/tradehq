# Routing & Nav Patch

1) Add route:
```jsx
import TradeFinder from "../pages/TradeFinder.jsx";
<Route path="/trade-finder" element={<TradeFinder/>} />
```

2) Add a nav link:
```jsx
<Link to="/trade-finder" className={navClass("/trade-finder")}>
  <span>Trade Finder</span>
</Link>
```
