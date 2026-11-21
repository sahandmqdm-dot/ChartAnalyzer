import React from 'react'; import { createRoot } from 'react-dom/client';
function App(){return <main style={{fontFamily:'system-ui',maxWidth:900,margin:'3rem auto',padding:'0 1rem'}}><h1>ChartAnalyzer Next</h1><p>Quantitative research · Foundation 0.1.0</p><section style={{border:'1px solid #aaa',borderRadius:12,padding:24}}><h2>Workspace foundation</h2><p>Scaffold running. Data, charts and research modules will be added as independent features.</p></section></main>}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
