export default async function handler(req,res){
  const key=process.env.BREVO_API_KEY;
  const notify=process.env.LEAD_NOTIFICATION_EMAIL;
  const senderId=Number(process.env.BREVO_SENDER_ID);
  const resendKey=process.env.RESEND_API_KEY;
  const resendAddress=process.env.RESEND_INBOUND_ADDRESS;
  const resendSecret=process.env.RESEND_WEBHOOK_SECRET;
  let brevoApi=false;
  let sender=false;
  let resendApi=false;
  let resendReceiving=false;
  if(key){
    try{
      const a=await fetch("https://api.brevo.com/v3/account",{headers:{accept:"application/json","api-key":key}});
      brevoApi=a.ok;
      if(a.ok&&senderId){
        const s=await fetch("https://api.brevo.com/v3/senders?limit=50&offset=0",{headers:{accept:"application/json","api-key":key}});
        if(s.ok){const data=await s.json();sender=Array.isArray(data.senders)&&data.senders.some(x=>Number(x.id)===senderId);}
      }
    }catch(e){}
  }
  if(resendKey){
    try{
      const a=await fetch("https://api.resend.com/emails/receiving?limit=1",{headers:{accept:"application/json","authorization":"Bearer "+resendKey}});
      resendApi=a.ok;
      resendReceiving=a.ok;
    }catch(e){}
  }
  return res.status(200).json({ok:true,service:"nerivo",brevo:!!key,brevoApi,notification:!!notify,sender:!!senderId,senderVerified:sender,outboundEmail:!!(key&&senderId),resend:!!resendKey,resendInboundAddress:!!resendAddress,resendWebhookSecret:!!resendSecret,resendApi,resendReceiving});
}