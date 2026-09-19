export default async function handler(req,res){
 if(req.method!=="GET")return res.status(405).json({ok:false});
 const expected=process.env.METRICS_SECRET,auth=req.headers.authorization||"";
 if(!expected||auth!=="Bearer "+expected)return res.status(401).json({ok:false});
 return res.status(200).json({ok:true,service:"nerivo",generatedAt:new Date().toISOString(),capabilities:{lead_ingest:true,inbound_webhook:true,local_agent:true,organic_content_engine:true,autonomous_engineering:true}});
}