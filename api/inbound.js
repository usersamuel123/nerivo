function secretOk(req){
  const expected=process.env.INBOUND_WEBHOOK_SECRET||process.env.BREVO_INBOUND_WEBHOOK_SECRET||"";
  if(!expected)return false;
  return (req.headers["x-nerivo-webhook-secret"]||req.headers["x-webhook-secret"]||"")===expected;
}
function addr(v){return typeof v==="string"?v:(v&&v.Address)||"";}
function resendFrom(v){
  if(typeof v!=="string")return "";
  const m=v.match(/<([^>]+)>/);
  return (m?m[1]:v).trim();
}
async function notify(subject,text){
  if(!(process.env.LEAD_NOTIFICATION_EMAIL&&process.env.BREVO_API_KEY&&process.env.BREVO_SENDER_ID))return;
  await fetch("https://api.brevo.com/v3/smtp/email",{
    method:"POST",
    headers:{"api-key":process.env.BREVO_API_KEY,"content-type":"application/json"},
    body:JSON.stringify({
      sender:{id:Number(process.env.BREVO_SENDER_ID)},
      to:[{email:process.env.LEAD_NOTIFICATION_EMAIL}],
      subject:"[NERIVO INBOUND] "+subject,
      textContent:text
    })
  });
}
export default async function handler(req,res){
 if(req.method!=="POST")return res.status(405).json({ok:false});
 if(!secretOk(req))return res.status(401).json({ok:false});
 try{
  const p=typeof req.body==="string"?JSON.parse(req.body||"{}"):(req.body||{});
  const isResend=p?.type==="email.received"&&p?.data;
  const rawItems=Array.isArray(p.items)?p.items:[p];
  const items=isResend?[p.data]:rawItems;
  let accepted=0;
  for(const item of items.slice(0,20)){
   const from=isResend?resendFrom(item.from):addr(item.From);
   const subject=String(isResend?item.subject:item.Subject||"").slice(0,300);
   const text=String(
     isResend
       ? (item.text||item.html||"")
       : (item.ExtractedMarkdownMessage||item.RawTextBody||"")
   ).slice(0,10000);
   if(!from||!text)continue;
   await notify(subject,"From: "+from+"\nSubject: "+subject+"\n\n"+text);
   accepted++;
  }
  return res.status(200).json({ok:true,accepted,provider:isResend?"resend":"brevo"});
 }catch(e){return res.status(400).json({ok:false,error:"invalid_payload"});}
}