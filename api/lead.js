const BREVO_API="https://api.brevo.com/v3";
const EMAIL_RE=/^[^\s@]+@[^\s@]+\.[^\s@]+$/;
function esc(v=""){return String(v).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
export default async function handler(req,res){
 if(req.method!=="POST")return res.status(405).json({ok:false,error:"method_not_allowed"});
 try{
  const b=typeof req.body==="string"?JSON.parse(req.body||"{}"):(req.body||{});
  if(b.website)return res.status(200).json({ok:true});
  const email=String(b.email||"").trim().toLowerCase(),name=String(b.name||"").trim().slice(0,120),agency=String(b.agency||"").trim().slice(0,160),source=String(b.source||"website").trim().slice(0,80);
  if(!EMAIL_RE.test(email)||!(b.privacy===true||b.consent===true))return res.status(400).json({ok:false,error:"invalid_lead"});
  if(!process.env.BREVO_API_KEY)return res.status(503).json({ok:false,error:"lead_service_not_configured"});
  const r=await fetch(BREVO_API+"/contacts",{method:"POST",headers:{"api-key":process.env.BREVO_API_KEY,"content-type":"application/json"},body:JSON.stringify({email,updateEnabled:true,attributes:{FIRSTNAME:name,COMPANY:agency,NERIVO_SOURCE:source}})});
  if(!r.ok&&r.status!==400)return res.status(502).json({ok:false,error:"brevo_error"});
  if(process.env.LEAD_NOTIFICATION_EMAIL&&process.env.BREVO_SENDER_ID){
   await fetch(BREVO_API+"/smtp/email",{method:"POST",headers:{"api-key":process.env.BREVO_API_KEY,"content-type":"application/json"},body:JSON.stringify({sender:{id:Number(process.env.BREVO_SENDER_ID)},to:[{email:process.env.LEAD_NOTIFICATION_EMAIL}],subject:"NERIVO — nuovo lead",htmlContent:"<p><strong>Nuovo lead</strong></p><p>Email: "+esc(email)+"</p><p>Nome: "+esc(name)+"</p><p>Agenzia: "+esc(agency)+"</p><p>Fonte: "+esc(source)+"</p>"})});
  }
  return res.status(200).json({ok:true,captured:true});
 }catch(e){return res.status(500).json({ok:false,error:"lead_ingest_failed"});}
}