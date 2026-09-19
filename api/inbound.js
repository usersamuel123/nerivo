function authorized(req){const expected=process.env.BREVO_INBOUND_WEBHOOK_SECRET;if(!expected)return false;return (req.headers["x-nerivo-webhook-secret"]||req.headers["x-webhook-secret"]||"")===expected;}
function addr(v){return typeof v==="string"?v:(v&&v.Address)||"";}
export default async function handler(req,res){
 if(req.method!=="POST")return res.status(405).json({ok:false});
 if(!authorized(req))return res.status(401).json({ok:false});
 try{
  const p=typeof req.body==="string"?JSON.parse(req.body||"{}"):(req.body||{}),items=Array.isArray(p.items)?p.items:[p];let accepted=0;
  for(const item of items.slice(0,20)){
   const from=addr(item.From),subject=String(item.Subject||"").slice(0,300),text=String(item.ExtractedMarkdownMessage||item.RawTextBody||"").slice(0,10000);
   if(!from||!text)continue;
   if(process.env.LEAD_NOTIFICATION_EMAIL&&process.env.BREVO_API_KEY&&process.env.BREVO_SENDER_ID)await fetch("https://api.brevo.com/v3/smtp/email",{method:"POST",headers:{"api-key":process.env.BREVO_API_KEY,"content-type":"application/json"},body:JSON.stringify({sender:{id:Number(process.env.BREVO_SENDER_ID)},to:[{email:process.env.LEAD_NOTIFICATION_EMAIL}],subject:"[NERIVO INBOUND] "+subject,textContent:"From: "+from+"\nSubject: "+subject+"\n\n"+text})});
   accepted++;
  }
  return res.status(200).json({ok:true,accepted});
 }catch(e){return res.status(400).json({ok:false,error:"invalid_payload"});}
}