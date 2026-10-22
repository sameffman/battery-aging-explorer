// Classic (non-module) script shared by the pages. ES modules and fetch() are blocked for pages
// opened from disk, so it says how to serve the project instead of leaving an empty chart.
if (location.protocol === 'file:') {
  document.getElementById('chart').innerHTML =
    '<div class="notice"><p>This page needs to be served over HTTP. From the project folder run ' +
    '<code>python3 -m http.server</code> and open ' +
    '<code>http://localhost:8000/' + location.pathname.split('/').pop() + '</code>.</p></div>';
}
