/* pflege-jobs collector (bookmarklet payload). Runs ON the target page (helios / stepstone / indeed / any career site),
   extracts JobPosting JSON-LD + job-like anchors, then hands the rows to the dashboard's /collect page via URL hash.
   No fetch from the page context (CSP-safe); the /collect page (our origin) writes to Supabase. */
(function(){
  var out={v:1,host:location.host,url:location.href,title:document.title,at:new Date().toISOString(),jobs:[],links:[]};
  function txt(s){return (s||"").replace(/<[^>]+>/g," ").replace(/\s+/g," ").trim();}
  document.querySelectorAll('script[type="application/ld+json"]').forEach(function(s){
    try{var d=JSON.parse(s.textContent);var st=Array.isArray(d)?d:[d];
      while(st.length){var n=st.pop();if(!n||typeof n!=="object")continue;
        if(n["@type"]==="JobPosting"||(Array.isArray(n["@type"])&&n["@type"].indexOf("JobPosting")>=0)){
          var loc=[].concat(n.jobLocation||[]).map(function(l){var a=(l&&l.address)||{};return {city:a.addressLocality||null,plz:a.postalCode||null,region:a.addressRegion||null};});
          out.jobs.push({title:txt(n.title),org:(n.hiringOrganization&&(n.hiringOrganization.name||n.hiringOrganization))||null,datePosted:n.datePosted||null,validThrough:n.validThrough||null,
            employmentType:n.employmentType||null,loc:loc,description:txt(n.description).slice(0,20000),url:n.url||location.href});}
        Object.keys(n).forEach(function(k){if(n[k]&&typeof n[k]==="object")st.push(n[k]);});}
    }catch(e){}});
  var gm=/\((?:m|w|d|x|i|gn)\s?[\/|*]\s?(?:m|w|d|x|i|gn)(?:\s?[\/|*]\s?(?:m|w|d|x|i|gn))?\)|\b[mwd]\/[mwd]\/[mwdx]\b/i;
  document.querySelectorAll("a[href]").forEach(function(a){var t=txt(a.innerText||a.textContent);if(t&&gm.test(t)&&out.links.length<400)out.links.push({t:t.slice(0,200),h:a.href});});
  if(!out.jobs.length&&!out.links.length){alert("pflege-jobs: keine Stellen auf dieser Seite gefunden (weder JSON-LD noch (m/w/d)-Links).");return;}
  var payload=btoa(unescape(encodeURIComponent(JSON.stringify(out))));
  var w=window.open("https://pflege-board.exe.xyz/collect.html#"+payload,"_blank");
  if(!w)alert("Popup blockiert – bitte Popups für diese Seite erlauben.");
})();
