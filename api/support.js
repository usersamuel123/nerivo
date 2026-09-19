const EMAIL_RE=/^[^\s@]+@[^\s@]+\.[^\s@]+$/;
function esc(v=""){return String(v).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
export default async function handler(req,res){
 if(req.method!=="POST")return res.status(405).json({ok:false});
 try{
  const b=typeof req.body==="string"?JSON.parse(req.body||"{}"):(req.body||{}),email=String(b.email||"").trim().toLowerCase(),issue=String(b.issue||"").trim().slice(0,5000);
  if(!EMAIL_RE.test(email)||!issue)return res.status(400).json({ok:false,error:"invalid_support_request"});
  if(!process.env.BREVO_API_KEY||!process.env.BREVO_SENDER_ID)return res.status(503).json({ok:false,error:"support_not_configured"});
  const r=await fetch("https://api.brevo.com/v3/smtp/email",{method:"POST",headers:{"api-key":process.env.BREVO_API_KEY,"content-type":"application/json"},body:JSON.stringify({sender:{id:Number(process.env.BREVO_SENDER_ID)},to:[{email}],subject:"NERIVO — richiesta di assistenza ricevuta",textContent:"Abbiamo ricevuto la tua richiesta. L'assistenza automatica NERIVO la sta elaborando. Se serve un intervento umano, verrai informato.",replyTo:{email}})});
  if(!r.ok)return res.status(502).json({ok:false,error:"email_failed"});
  if(process.env.LEAD_NOTIFICATION_EMAIL)await fetch("https://api.brevo.com/v3/smtp/email",{method:"POST",headers:{"api-key":process.env.BREVO_API_KEY,"content-type":"application/json"},body:JSON.stringify({sender:{id:Number(process.env.BREVO_SENDER_ID)},to:[{email:process.env.LEAD_NOTIFICATION_EMAIL}],subject:"NERIVO — ticket support",htmlContent:"<p>Cliente: "+esc(email)+"</p><p>"+esc(issue).replace(/\n/g,"<br>")+"</p>"})});
  return res.status(200).json({ok:true,received:true});
 }catch(e){return res.status(500).json({ok:false,error:"support_failed"});}
}